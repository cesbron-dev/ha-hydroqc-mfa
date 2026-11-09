"""Compagnon qui complète automatiquement la réauthentification MFA de hydroqc."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_GMAIL_APP_PASSWORD, CONF_GMAIL_USERNAME
from .gmail import GmailInbox
from .lock import RenewalLock
from .renewer import Renewer

PLATFORMS = [Platform.BUTTON, Platform.SENSOR]

type HydroqcMfaConfigEntry = ConfigEntry[Renewer]


async def async_setup_entry(hass: HomeAssistant, entry: HydroqcMfaConfigEntry) -> bool:
    """Démarre la surveillance des flux de réauthentification hydroqc."""

    def inbox_factory() -> GmailInbox:
        return GmailInbox(entry.data[CONF_GMAIL_USERNAME], entry.data[CONF_GMAIL_APP_PASSWORD])

    renewer = Renewer(hass, entry, RenewalLock(hass), inbox_factory)
    entry.runtime_data = renewer
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    try:
        await renewer.async_start()
    except BaseException:
        # Pas de minuterie ni de surveillance orpheline : sinon un second
        # renouveleur coexisterait avec celui d'une prochaine configuration.
        await renewer.async_stop()
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        raise
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HydroqcMfaConfigEntry) -> bool:
    """Arrête la surveillance (annule une tentative en cours)."""
    await entry.runtime_data.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: HydroqcMfaConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
