"""Verrou persistant : au plus une tentative automatique par entrée hydroqc."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

STORAGE_KEY = "hydroqc_mfa.lock"
STORAGE_VERSION = 1

ARMED = "armed"
IN_PROGRESS = "in_progress"
FAILED = "failed"


class RenewalLock:
    """État persistant du verrou, par identifiant d'entrée hydroqc."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, dict[str, str | None]]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY
        )
        self._data: dict[str, dict[str, str | None]] = {}

    async def async_load(self) -> list[str]:
        """Charge l'état ; renvoie les entrées `in_progress` passées à `failed`."""
        self._data = await self._store.async_load() or {}
        interrupted = [
            entry_id
            for entry_id, item in self._data.items()
            if item.get("state") == IN_PROGRESS
        ]
        for entry_id in interrupted:
            self._data[entry_id] = {"state": FAILED, "reason": "interrupted"}
        if interrupted:
            await self._store.async_save(self._data)
        return interrupted

    def is_armed(self, entry_id: str) -> bool:
        """Une entrée inconnue est armée."""
        return self._data.get(entry_id, {}).get("state", ARMED) == ARMED

    async def async_begin(self, entry_id: str) -> None:
        """Marque la tentative en cours ; revient une fois écrit sur disque."""
        await self._set(entry_id, IN_PROGRESS, None)

    async def async_fail(self, entry_id: str, reason: str) -> None:
        await self._set(entry_id, FAILED, reason)

    async def async_arm(self, entry_id: str) -> None:
        await self._set(entry_id, ARMED, None)

    async def _set(self, entry_id: str, state: str, reason: str | None) -> None:
        self._data[entry_id] = {"state": state, "reason": reason}
        await self._store.async_save(self._data)
