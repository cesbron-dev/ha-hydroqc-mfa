"""Compagnon qui complète automatiquement la réauthentification MFA de hydroqc."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_GMAIL_APP_PASSWORD, CONF_GMAIL_USERNAME
from .gmail import GmailInbox
from .lock import RenewalLock
from .renewer import Renewer

type HydroqcMfaConfigEntry = ConfigEntry[Renewer]


async def async_setup_entry(hass: HomeAssistant, entry: HydroqcMfaConfigEntry) -> bool:
    """Démarre la surveillance des flux de réauthentification hydroqc."""

    def inbox_factory() -> GmailInbox:
        return GmailInbox(entry.data[CONF_GMAIL_USERNAME], entry.data[CONF_GMAIL_APP_PASSWORD])

    renewer = Renewer(hass, entry, RenewalLock(hass), inbox_factory)
    entry.runtime_data = renewer
    await renewer.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HydroqcMfaConfigEntry) -> bool:
    """Arrête la surveillance (annule une tentative en cours)."""
    await entry.runtime_data.async_stop()
    return True


async def _async_options_updated(hass: HomeAssistant, entry: HydroqcMfaConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
