"""Tests des capteurs de suivi."""

from datetime import UTC, datetime

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from custom_components.hydroqc_mfa.const import DOMAIN, SIGNAL_RESULT

RENEWAL = "sensor.hydroqc_mfa_dernier_renouvellement"
ATTEMPT = "sensor.hydroqc_mfa_derniere_tentative"
WHEN = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"gmail_username": "a@gmail.com", "gmail_app_password": "secret"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_sensors_update_on_success(hass: HomeAssistant) -> None:
    """Un succès met à jour les deux capteurs."""
    await _setup(hass)
    assert hass.states.get(RENEWAL).state == "unknown"

    async_dispatcher_send(hass, SIGNAL_RESULT, "success", WHEN)
    await hass.async_block_till_done()

    assert hass.states.get(RENEWAL).state == WHEN.isoformat()
    assert hass.states.get(ATTEMPT).state == "success"


async def test_failure_updates_only_result(hass: HomeAssistant) -> None:
    """Un échec ne touche pas le dernier renouvellement."""
    await _setup(hass)
    async_dispatcher_send(hass, SIGNAL_RESULT, "success", WHEN)
    await hass.async_block_till_done()

    later = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    async_dispatcher_send(hass, SIGNAL_RESULT, "code_timeout", later)
    await hass.async_block_till_done()

    assert hass.states.get(RENEWAL).state == WHEN.isoformat()
    assert hass.states.get(ATTEMPT).state == "code_timeout"


async def test_sensors_restored(hass: HomeAssistant) -> None:
    """Les valeurs sont restaurées au redémarrage."""
    mock_restore_cache(
        hass,
        [State(RENEWAL, WHEN.isoformat()), State(ATTEMPT, "gmail_unavailable")],
    )
    await _setup(hass)

    assert hass.states.get(RENEWAL).state == WHEN.isoformat()
    assert hass.states.get(ATTEMPT).state == "gmail_unavailable"
