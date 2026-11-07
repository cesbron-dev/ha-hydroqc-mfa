"""Tests de l'orchestration de la réauthentification."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_config_flow

from custom_components.hydroqc_mfa.const import (
    CONF_GMAIL_APP_PASSWORD,
    CONF_GMAIL_USERNAME,
    CONF_PAUSED,
    DOMAIN,
    SIGNAL_RESULT,
)
from custom_components.hydroqc_mfa.gmail import GmailInbox
from custom_components.hydroqc_mfa.lock import STORAGE_KEY, RenewalLock
from custom_components.hydroqc_mfa.notify import NOTIFICATION_ID
from custom_components.hydroqc_mfa.renewer import Renewer

from .fake_hydroqc import (
    HQ_CODE,
    HQ_PASSWORD,
    FakeHydroFlow,
    HydroBehavior,
    async_open_reauth,
    hydro_entry,
    register_fake_hydroqc,
)
from .fake_imap import PASSWORD, FakeImap, hq_message

GMAIL_USER = "olivier@gmail.com"


@pytest.fixture(autouse=True)
def fast_timings() -> None:
    with (
        patch("custom_components.hydroqc_mfa.renewer.POLL_SECONDS", 0.01),
        patch("custom_components.hydroqc_mfa.renewer.WAIT_SECONDS", 0.1),
    ):
        yield


@pytest.fixture
def imap() -> FakeImap:
    fake = FakeImap()
    with patch("custom_components.hydroqc_mfa.gmail.imaplib.IMAP4_SSL", new=fake):
        yield fake


@pytest.fixture
def behavior() -> HydroBehavior:
    behavior = HydroBehavior()
    FakeHydroFlow.behavior = behavior
    return behavior


@pytest.fixture
async def hydro(hass: HomeAssistant, behavior: HydroBehavior) -> AsyncIterator[MockConfigEntry]:
    register_fake_hydroqc(hass)
    with mock_config_flow("hydroqc", FakeHydroFlow):
        yield hydro_entry(hass)


@pytest.fixture
def companion(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_GMAIL_USERNAME: GMAIL_USER, CONF_GMAIL_APP_PASSWORD: PASSWORD},
        options={},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def results(hass: HomeAssistant) -> list[str]:
    seen: list[str] = []
    async_dispatcher_connect(hass, SIGNAL_RESULT, lambda reason, _when: seen.append(reason))
    return seen


@pytest.fixture
async def make_renewer(hass: HomeAssistant, companion: MockConfigEntry, imap: FakeImap):
    created: list[Renewer] = []

    def _make() -> Renewer:
        renewer = Renewer(
            hass, companion, RenewalLock(hass), lambda: GmailInbox(GMAIL_USER, PASSWORD)
        )
        created.append(renewer)
        return renewer

    yield _make
    for renewer in created:
        await renewer.async_stop()


async def _settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)


def _lock_state(hass_storage: dict, entry_id: str) -> dict:
    return hass_storage[STORAGE_KEY]["data"][entry_id]


def _notified(hass: HomeAssistant) -> bool:
    return NOTIFICATION_ID in hass.data["persistent_notification"]


def _mail_on_password(behavior: HydroBehavior, imap: FakeImap, code: str = HQ_CODE) -> None:
    behavior.on_password = lambda: imap.add(hq_message(code))


async def test_success_submits_password_then_code(
    hass, hydro, behavior, imap, make_renewer, results, hass_storage
):
    _mail_on_password(behavior, imap)
    await async_open_reauth(hass, hydro)
    await make_renewer().async_start()
    await _settle(hass)

    assert results == ["success"]
    assert behavior.passwords == [HQ_PASSWORD]
    assert behavior.codes == [HQ_CODE]
    # Pas de réarmement tant que l'entrée hydroqc n'est pas revenue à LOADED.
    assert _lock_state(hass_storage, hydro.entry_id)["state"] == "succeeded"
    assert any(c.startswith("STORE") and "\\Trash" in c for c in imap.commands)
    assert imap.logged_out
    assert not hass.config_entries.flow.async_progress_by_handler("hydroqc")


async def test_success_without_trash_option(
    hass, hydro, behavior, imap, make_renewer, results, companion
):
    hass.config_entries.async_update_entry(companion, options={"trash_used_email": False})
    _mail_on_password(behavior, imap)
    await async_open_reauth(hass, hydro)
    await make_renewer().async_start()
    await _settle(hass)

    assert results == ["success"]
    assert not any(c.startswith("STORE") for c in imap.commands)


async def test_abort_after_password_is_success(
    hass, hydro, behavior, imap, make_renewer, results
):
    behavior.abort_after_password = True
    await async_open_reauth(hass, hydro)
    await make_renewer().async_start()
    await _settle(hass)

    assert results == ["success"]
    assert behavior.codes == []


async def test_checkpoint_before_password(hass, hydro, behavior, imap, make_renewer):
    seen_at_password: list[list[str]] = []

    def on_password() -> None:
        seen_at_password.append(list(imap.commands))
        imap.add(hq_message(HQ_CODE))

    behavior.on_password = on_password
    await async_open_reauth(hass, hydro)
    await make_renewer().async_start()
    await _settle(hass)

    assert seen_at_password == [["LOGIN", "SELECT INBOX readonly=True"]]


async def test_code_timeout_notifies_and_locks(
    hass, hydro, behavior, imap, make_renewer, results, hass_storage
):
    await async_open_reauth(hass, hydro)
    await make_renewer().async_start()
    await _settle(hass)

    assert results == ["code_timeout"]
    assert _notified(hass)
    assert _lock_state(hass_storage, hydro.entry_id) == {
        "state": "failed",
        "reason": "code_timeout",
    }
    assert behavior.codes == []
    assert imap.logged_out
    # Le flux reste ouvert pour qu'Olivier le termine à la main.
    assert hass.config_entries.flow.async_progress_by_handler("hydroqc")


async def test_password_rejected(
    hass, hydro, behavior, imap, make_renewer, results, hass_storage
):
    behavior.password = "autre"
    await async_open_reauth(hass, hydro)
    await make_renewer().async_start()
    await _settle(hass)

    assert results == ["password_rejected"]
    assert behavior.passwords == [HQ_PASSWORD]
    assert not any(c.startswith(("NOOP", "SEARCH")) for c in imap.commands)
    assert _lock_state(hass_storage, hydro.entry_id)["reason"] == "password_rejected"
    assert _notified(hass)


async def test_unexpected_form_after_password(
    hass, hydro, behavior, imap, make_renewer, results
):
    behavior.unexpected_step_after_password = True
    await async_open_reauth(hass, hydro)
    await make_renewer().async_start()
    await _settle(hass)

    assert results == ["unexpected_form"]
    assert behavior.codes == []


async def test_code_rejected_no_second_submission(
    hass, hydro, behavior, imap, make_renewer, results, hass_storage
):
    behavior.code = "654321"
    _mail_on_password(behavior, imap)
    await async_open_reauth(hass, hydro)
    await make_renewer().async_start()
    await _settle(hass)

    assert results == ["code_rejected"]
    assert behavior.codes == [HQ_CODE]
    assert not any(c.startswith("STORE") for c in imap.commands)
    assert _lock_state(hass_storage, hydro.entry_id)["reason"] == "code_rejected"


async def test_gmail_down_no_hydro_request(hass, hydro, behavior, make_renewer, results):
    def unreachable(*_args, **_kwargs):
        raise OSError("réseau")

    await async_open_reauth(hass, hydro)
    with patch("custom_components.hydroqc_mfa.gmail.imaplib.IMAP4_SSL", new=unreachable):
        await make_renewer().async_start()
        await _settle(hass)

    assert results == ["gmail_unavailable"]
    assert behavior.passwords == []
    assert _notified(hass)


async def test_gmail_auth_failed_no_hydro_request(
    hass, hydro, behavior, imap, make_renewer, results
):
    imap.password = "autre"
    await async_open_reauth(hass, hydro)
    await make_renewer().async_start()
    await _settle(hass)

    assert results == ["gmail_auth_failed"]
    assert behavior.passwords == []


async def test_flow_exception_is_unexpected(
    hass, hydro, behavior, imap, make_renewer, results, caplog
):
    behavior.raise_on_password = True
    await async_open_reauth(hass, hydro)
    await make_renewer().async_start()
    await _settle(hass)

    assert results == ["unexpected"]
    assert HQ_PASSWORD not in caplog.text
    assert imap.logged_out


async def test_missing_hydro_password_is_unexpected(
    hass, hydro, behavior, imap, make_renewer, results, hass_storage
):
    hass.config_entries.async_update_entry(hydro, data={"username": "olivier@example.com"})
    await async_open_reauth(hass, hydro)
    await make_renewer().async_start()
    await _settle(hass)

    assert results == ["unexpected"]
    assert behavior.passwords == []
    assert _lock_state(hass_storage, hydro.entry_id)["state"] == "failed"


async def test_no_retry_after_failure(hass, hydro, behavior, imap, make_renewer, results):
    behavior.password = "autre"
    flow_id = await async_open_reauth(hass, hydro)
    renewer = make_renewer()
    await renewer.async_start()
    await _settle(hass)
    assert results == ["password_rejected"]

    hass.config_entries.flow.async_abort(flow_id)
    await async_open_reauth(hass, hydro)
    await renewer.async_check()
    await _settle(hass)

    assert results == ["password_rejected"]
    assert behavior.passwords == [HQ_PASSWORD]


async def test_rearm_on_loaded(
    hass, hydro, behavior, imap, make_renewer, results, hass_storage
):
    flow_id = await async_open_reauth(hass, hydro)
    renewer = make_renewer()
    await renewer.async_start()
    await _settle(hass)
    assert results == ["code_timeout"]
    assert _notified(hass)

    # Olivier termine à la main ; l'entrée hydroqc revient chargée.
    hass.config_entries.flow.async_abort(flow_id)
    hydro.mock_state(hass, ConfigEntryState.LOADED)
    await _settle(hass)
    assert _lock_state(hass_storage, hydro.entry_id)["state"] == "armed"
    assert not _notified(hass)

    _mail_on_password(behavior, imap)
    await async_open_reauth(hass, hydro)
    await renewer.async_check()
    await _settle(hass)
    assert results == ["code_timeout", "success"]
    assert behavior.passwords == [HQ_PASSWORD, HQ_PASSWORD]


async def test_success_then_reauth_on_reload_no_second_attempt(
    hass, hydro, behavior, imap, make_renewer, results, hass_storage
):
    _mail_on_password(behavior, imap)
    flow_id = await async_open_reauth(hass, hydro)
    renewer = make_renewer()
    await renewer.async_start()
    await _settle(hass)
    assert results == ["success"]
    assert not _notified(hass)
    assert not any(f["flow_id"] == flow_id for f in hass.config_entries.flow.async_progress())

    # Le rechargement qui suit le succès échoue : ConfigEntryAuthFailed → nouveau flux.
    hydro.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    hydro.mock_state(hass, ConfigEntryState.SETUP_ERROR)
    await async_open_reauth(hass, hydro)
    await renewer.async_check()
    await _settle(hass)

    assert results == ["success"]
    assert behavior.passwords == [HQ_PASSWORD]
    assert _lock_state(hass_storage, hydro.entry_id)["state"] == "succeeded"


async def test_success_then_loaded_rearms(
    hass, hydro, behavior, imap, make_renewer, results, hass_storage
):
    _mail_on_password(behavior, imap)
    await async_open_reauth(hass, hydro)
    renewer = make_renewer()
    await renewer.async_start()
    await _settle(hass)
    assert results == ["success"]
    assert _lock_state(hass_storage, hydro.entry_id)["state"] == "succeeded"

    hydro.mock_state(hass, ConfigEntryState.LOADED)
    await _settle(hass)
    assert _lock_state(hass_storage, hydro.entry_id)["state"] == "armed"

    await async_open_reauth(hass, hydro)
    await renewer.async_check()
    await _settle(hass)
    assert results == ["success", "success"]
    assert behavior.passwords == [HQ_PASSWORD, HQ_PASSWORD]


async def test_loaded_during_attempt_ends_armed(
    hass, hydro, behavior, imap, make_renewer, results, hass_storage
):
    def on_password() -> None:
        imap.add(hq_message(HQ_CODE))
        # Le rechargement hydroqc atteint LOADED avant l'écriture finale de la tentative.
        hydro.mock_state(hass, ConfigEntryState.LOADED)

    behavior.on_password = on_password
    await async_open_reauth(hass, hydro)
    await make_renewer().async_start()
    await _settle(hass)

    assert results == ["success"]
    assert _lock_state(hass_storage, hydro.entry_id)["state"] == "armed"
    assert not _notified(hass)


async def test_ignores_flow_at_mfa_step(hass, hydro, behavior, imap, make_renewer, results):
    flow_id = await async_open_reauth(hass, hydro)
    result = await hass.config_entries.flow.async_configure(flow_id, {"password": HQ_PASSWORD})
    assert result["step_id"] == "mfa"

    await make_renewer().async_start()
    await _settle(hass)

    assert results == []
    assert behavior.passwords == [HQ_PASSWORD]
    assert imap.commands == []


async def test_no_attempt_after_restart_mid_attempt(
    hass, hydro, behavior, imap, make_renewer, results, hass_storage
):
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "key": STORAGE_KEY,
        "data": {hydro.entry_id: {"state": "in_progress", "reason": None}},
    }
    await async_open_reauth(hass, hydro)
    await make_renewer().async_start()
    await _settle(hass)

    assert behavior.passwords == []
    assert imap.commands == []
    assert _notified(hass)
    assert _lock_state(hass_storage, hydro.entry_id) == {
        "state": "failed",
        "reason": "interrupted",
    }


async def test_concurrent_checks_single_attempt(
    hass, hydro, behavior, imap, make_renewer, results
):
    _mail_on_password(behavior, imap)
    await async_open_reauth(hass, hydro)
    renewer = make_renewer()
    await renewer.async_start()
    await asyncio.gather(renewer.async_check(), renewer.async_check(), renewer.async_check())
    await _settle(hass)
    await renewer.async_check()
    await _settle(hass)

    assert results == ["success"]
    assert behavior.passwords == [HQ_PASSWORD]
    assert behavior.codes == [HQ_CODE]


async def test_periodic_check_picks_up_new_flow(
    hass, hydro, behavior, imap, make_renewer, results
):
    _mail_on_password(behavior, imap)
    renewer = make_renewer()
    with patch("custom_components.hydroqc_mfa.renewer.CHECK_INTERVAL_SECONDS", 0.02):
        await renewer.async_start()
    await async_open_reauth(hass, hydro)
    for _ in range(100):
        if results:
            break
        await asyncio.sleep(0.01)
    await _settle(hass)

    assert results == ["success"]


async def test_paused_does_nothing(
    hass, hydro, behavior, imap, make_renewer, results, companion
):
    hass.config_entries.async_update_entry(companion, options={CONF_PAUSED: True})
    await async_open_reauth(hass, hydro)
    renewer = make_renewer()
    await renewer.async_start()
    await renewer.async_check()
    await _settle(hass)

    assert results == []
    assert behavior.passwords == []
    assert imap.commands == []


async def test_unload_cancels_attempt(
    hass, hydro, behavior, imap, make_renewer, results, hass_storage
):
    submitted = asyncio.Event()
    behavior.on_password = submitted.set
    await async_open_reauth(hass, hydro)
    renewer = make_renewer()
    with patch("custom_components.hydroqc_mfa.renewer.WAIT_SECONDS", 30):
        await renewer.async_start()
        await asyncio.wait_for(submitted.wait(), 5)
        await renewer.async_stop()

    assert results == []
    assert behavior.codes == []
    assert imap.logged_out
    # Jamais réarmé : au prochain démarrage, la tentative sera « interrupted ».
    assert _lock_state(hass_storage, hydro.entry_id)["state"] == "in_progress"
    assert not logging.getLogger("hydroqc.httpclient").filters


async def test_no_secrets_in_logs(
    hass, hydro, behavior, imap, make_renewer, results, caplog
):
    caplog.set_level(logging.DEBUG)
    _mail_on_password(behavior, imap)
    await async_open_reauth(hass, hydro)
    renewer = make_renewer()
    await renewer.async_start()
    await _settle(hass)
    assert results == ["success"]
    hydro.mock_state(hass, ConfigEntryState.LOADED)
    await _settle(hass)

    # Deuxième tentative, refusée : le code soumis ne correspond pas.
    behavior.code = "654321"
    await async_open_reauth(hass, hydro)
    await renewer.async_check()
    await _settle(hass)
    assert results == ["success", "code_rejected"]

    logging.getLogger("hydroqc.httpclient").debug("hors tentative visible")

    assert "hors tentative visible" in caplog.text
    assert "hq.example" not in caplog.text
    for secret in (HQ_CODE, HQ_PASSWORD, PASSWORD):
        assert secret not in caplog.text
