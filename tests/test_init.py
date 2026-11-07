"""Tests de chargement de l'intégration."""

import logging
from datetime import timedelta
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

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


async def test_start_failure_leaves_no_timer(hass: HomeAssistant) -> None:
    """Un échec au démarrage ne laisse ni minuterie ni surveillance orpheline."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"gmail_username": "a@gmail.com", "gmail_app_password": "secret"},
    )
    entry.add_to_hass(hass)
    calls: list[None] = []

    async def failing_check(self: Renewer) -> None:
        calls.append(None)
        if len(calls) == 1:
            raise RuntimeError("panne au démarrage")

    with patch.object(Renewer, "async_check", failing_check):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_ERROR
        renewer = entry.runtime_data
        assert renewer._unsub_interval is None
        assert not logging.getLogger("hydroqc.httpclient").filters

        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=300))
        await hass.async_block_till_done()
    assert len(calls) == 1
