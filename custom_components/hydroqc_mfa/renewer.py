"""Orchestration : complète le flux de réauthentification hydroqc avec le code reçu par Gmail."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from contextvars import ContextVar
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry, ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CHECK_INTERVAL_SECONDS,
    CONF_NOTIFY_SERVICE,
    CONF_PAUSED,
    CONF_TRASH,
    HYDROQC_DOMAIN,
    POLL_SECONDS,
    SIGNAL_RESULT,
    WAIT_SECONDS,
)
from .gmail import GmailCode, GmailError, GmailInbox
from .lock import RenewalLock
from .notify import async_clear_failure, async_notify_failure

_LOGGER = logging.getLogger(__name__)

SUCCESS = "success"
HTTPCLIENT_LOGGER = "hydroqc.httpclient"

_ATTEMPT_ACTIVE: ContextVar[bool] = ContextVar("hydroqc_mfa_attempt_active", default=False)


class _AttemptLogFilter(logging.Filter):
    """Écarte les journaux de `hydroqc.httpclient` émis pendant une tentative."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not _ATTEMPT_ACTIVE.get()


_LOG_FILTER = _AttemptLogFilter()


def _is_success(result: Any) -> bool:
    return result.get("type") is FlowResultType.ABORT and result.get("reason") == "reauth_successful"


def _is_form(result: Any, step_id: str) -> bool:
    return result.get("type") is FlowResultType.FORM and result.get("step_id") == step_id


class Renewer:
    """Surveille les flux `reauth` de hydroqc ; une seule tentative par entrée hydroqc."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        lock: RenewalLock,
        inbox_factory: Callable[[], GmailInbox],
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._lock = lock
        self._inbox_factory = inbox_factory
        self._guards: dict[str, asyncio.Lock] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._watched: dict[str, CALLBACK_TYPE] = {}
        self._unsub_interval: CALLBACK_TYPE | None = None
        self._installed_filter = False
        self._stopping = False

    @property
    def _notify_service(self) -> str | None:
        return self._entry.options.get(CONF_NOTIFY_SERVICE)

    async def async_start(self) -> None:
        """Charge le verrou, s'abonne aux entrées hydroqc et lance la surveillance."""
        logger = logging.getLogger(HTTPCLIENT_LOGGER)
        if _LOG_FILTER not in logger.filters:
            logger.addFilter(_LOG_FILTER)
            self._installed_filter = True
        for _entry_id in await self._lock.async_load():
            await async_notify_failure(self.hass, self._notify_service, "interrupted")
        self._unsub_interval = async_track_time_interval(
            self.hass, self._async_periodic, timedelta(seconds=CHECK_INTERVAL_SECONDS)
        )
        await self.async_check()

    async def async_stop(self) -> None:
        """Désabonne, annule la tentative en cours et attend sa fin."""
        self._stopping = True
        if self._unsub_interval is not None:
            self._unsub_interval()
            self._unsub_interval = None
        for unsub in self._watched.values():
            with suppress(ValueError):
                unsub()
        self._watched.clear()
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._installed_filter:
            logging.getLogger(HTTPCLIENT_LOGGER).removeFilter(_LOG_FILTER)
            self._installed_filter = False

    async def async_check(self) -> None:
        """Lance une tentative pour chaque flux `reauth_confirm` dont l'entrée est armée."""
        if self._stopping:
            return
        self._async_watch_hydro_entries()
        if self._entry.options.get(CONF_PAUSED, False):
            return
        flows = self.hass.config_entries.flow.async_progress_by_handler(
            HYDROQC_DOMAIN, match_context={"source": SOURCE_REAUTH}
        )
        for flow in flows:
            if flow.get("step_id") != "reauth_confirm":
                continue
            entry_id = flow.get("context", {}).get("entry_id")
            if not entry_id or not self._lock.is_armed(entry_id):
                continue
            guard = self._guards.setdefault(entry_id, asyncio.Lock())
            if guard.locked():
                continue
            # Démarrage immédiat : le verrou asyncio est pris avant le prochain `await`.
            task = self.hass.async_create_background_task(
                self._async_guarded_renew(guard, flow["flow_id"], entry_id),
                f"{self._entry.domain} renew {entry_id}",
                eager_start=True,
            )
            if not task.done():
                self._tasks[entry_id] = task
                task.add_done_callback(partial(self._async_forget_task, entry_id))

    async def async_renew(self, flow_id: str, hydro_entry_id: str) -> str:
        """Une tentative complète ; renvoie `success` ou une raison d'échec."""
        token = _ATTEMPT_ACTIVE.set(True)
        try:
            await self._lock.async_begin(hydro_entry_id)
            reason = await self._async_attempt(flow_id, hydro_entry_id)
            if reason == SUCCESS:
                await self._lock.async_arm(hydro_entry_id)
                await async_clear_failure(self.hass)
            else:
                await self._lock.async_fail(hydro_entry_id, reason)
                await async_notify_failure(self.hass, self._notify_service, reason)
        finally:
            _ATTEMPT_ACTIVE.reset(token)
        async_dispatcher_send(self.hass, SIGNAL_RESULT, reason, dt_util.utcnow())
        return reason

    async def _async_attempt(self, flow_id: str, hydro_entry_id: str) -> str:
        """Gmail d'abord ; aucune requête Hydro-Québec si Gmail est inutilisable."""
        inbox: GmailInbox | None = None
        flow_manager = self.hass.config_entries.flow
        try:
            hydro = self.hass.config_entries.async_get_entry(hydro_entry_id)
            password = (
                hydro.data.get("password")
                if hydro is not None and hydro.domain == HYDROQC_DOMAIN
                else None
            )
            if not isinstance(password, str) or not password:
                return "unexpected"
            inbox = self._inbox_factory()
            await self.hass.async_add_executor_job(inbox.connect)
            await self.hass.async_add_executor_job(inbox.checkpoint)

            result = await flow_manager.async_configure(flow_id, {"password": password})
            if _is_success(result):
                return SUCCESS
            if _is_form(result, "reauth_confirm") and result.get("errors"):
                return "password_rejected"
            if not _is_form(result, "mfa") or result.get("errors"):
                return "unexpected_form"

            receipt = await self._async_wait_for_code(inbox)
            if receipt is None:
                return "code_timeout"
            result = await flow_manager.async_configure(flow_id, {"mfa_code": receipt.code})
            if not _is_success(result):
                return "code_rejected"
            if self._entry.options.get(CONF_TRASH, True):
                try:
                    await self.hass.async_add_executor_job(inbox.trash, receipt)
                except Exception:  # noqa: BLE001 - succès malgré tout, sans détail
                    _LOGGER.warning("Impossible de mettre le courriel du code à la corbeille")
            return SUCCESS
        except GmailError as err:
            return err.reason
        except Exception:  # noqa: BLE001 - jamais de détail : il peut contenir un secret
            _LOGGER.warning("Réauthentification hydroqc interrompue par une erreur inattendue")
            return "unexpected"
        finally:
            if inbox is not None:
                with suppress(Exception):
                    await self.hass.async_add_executor_job(inbox.close)

    async def _async_wait_for_code(self, inbox: GmailInbox) -> GmailCode | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + WAIT_SECONDS
        while True:
            receipt = await self.hass.async_add_executor_job(inbox.poll)
            if receipt is not None:
                return receipt
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            await asyncio.sleep(min(POLL_SECONDS, remaining))

    async def _async_guarded_renew(
        self, guard: asyncio.Lock, flow_id: str, hydro_entry_id: str
    ) -> None:
        async with guard:
            if self._stopping or not self._lock.is_armed(hydro_entry_id):
                return
            try:
                await self.async_renew(flow_id, hydro_entry_id)
            except Exception:  # noqa: BLE001 - p. ex. écriture du verrou impossible
                _LOGGER.warning("Tentative de réauthentification hydroqc abandonnée")

    @callback
    def _async_forget_task(self, entry_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(entry_id) is task:
            del self._tasks[entry_id]

    @callback
    def _async_watch_hydro_entries(self) -> None:
        for hydro in self.hass.config_entries.async_entries(HYDROQC_DOMAIN):
            if hydro.entry_id not in self._watched:
                self._watched[hydro.entry_id] = hydro.async_on_state_change(
                    partial(self._async_hydro_state_changed, hydro)
                )

    @callback
    def _async_hydro_state_changed(self, hydro: ConfigEntry) -> None:
        if self._stopping:
            return
        self._entry.async_create_task(
            self.hass, self._async_on_hydro_state(hydro), f"{self._entry.domain} hydroqc state"
        )

    async def _async_on_hydro_state(self, hydro: ConfigEntry) -> None:
        """`LOADED` = session hydroqc fonctionnelle : réarme et efface l'alerte."""
        if hydro.state is ConfigEntryState.LOADED and not self._lock.is_armed(hydro.entry_id):
            await self._lock.async_arm(hydro.entry_id)
            await async_clear_failure(self.hass)
        await self.async_check()

    async def _async_periodic(self, _now: datetime) -> None:
        await self.async_check()
