"""Tests de chargement de l'intégration."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydroqc_mfa.const import DOMAIN


async def test_setup_and_unload(hass: HomeAssistant) -> None:
    """L'entrée se charge puis se décharge."""
    entry = MockConfigEntry(domain=DOMAIN, data={"gmail_username": "a@gmail.com"})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
