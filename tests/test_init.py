"""Tests de chargement de l'intégration."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydroqc_mfa.const import DOMAIN
from custom_components.hydroqc_mfa.renewer import Renewer


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


async def test_setup_starts_renewer_and_reloads_on_options(hass: HomeAssistant) -> None:
    """Le renouveleur est créé au chargement et recréé quand les options changent."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"gmail_username": "a@gmail.com", "gmail_app_password": "secret"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    first = entry.runtime_data
    assert isinstance(first, Renewer)

    hass.config_entries.async_update_entry(entry, options={"paused": True})
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not first

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
