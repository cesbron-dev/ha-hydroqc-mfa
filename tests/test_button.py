"""Bouton « Tester la connexion Gmail » et son capteur de résultat."""

import logging
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from custom_components.hydroqc_mfa.const import DOMAIN

from .fake_imap import PASSWORD, FakeImap

BUTTON = "button.hydroqc_mfa_tester_gmail"
RESULT = "sensor.hydroqc_mfa_test_gmail"
ATTEMPT = "sensor.hydroqc_mfa_derniere_tentative"
IMAP = "custom_components.hydroqc_mfa.gmail.imaplib.IMAP4_SSL"


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"gmail_username": "a@gmail.com", "gmail_app_password": PASSWORD},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _press(hass: HomeAssistant) -> None:
    await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)
    await hass.async_block_till_done()


async def test_press_success_reports_ok(hass: HomeAssistant) -> None:
    fake = FakeImap()
    with patch(IMAP, new=fake):
        await _setup(hass)
        await _press(hass)

    state = hass.states.get(RESULT)
    assert state.state == "ok"
    assert state.attributes["detail"] == ""
    assert state.attributes["tested_at"]
    assert fake.logged_out
    # Le test ne lit aucun message et ne touche pas au suivi des renouvellements.
    assert not any(c.startswith(("FETCH", "STORE")) for c in fake.commands)
    assert hass.states.get(ATTEMPT).state == "unknown"


async def test_press_network_failure_reports_reason_and_detail(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    def unreachable(*_args, **_kwargs):
        raise TimeoutError("détail serveur secret")

    await _setup(hass)
    with patch(IMAP, new=unreachable), pytest.raises(HomeAssistantError) as err:
        await _press(hass)
    await hass.async_block_till_done()

    state = hass.states.get(RESULT)
    assert state.state == "gmail_unavailable"
    assert state.attributes["detail"] == "connexion : TimeoutError"
    assert err.value.translation_key == "gmail_test_failed"
    assert "Test de connexion Gmail en échec (gmail_unavailable ; connexion : TimeoutError)" in (
        caplog.text
    )
    assert "détail serveur secret" not in caplog.text
    assert PASSWORD not in caplog.text
    assert hass.states.get(ATTEMPT).state == "unknown"


async def test_press_bad_password_reports_auth_failed(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeImap(password="autre-mot-de-passe")
    await _setup(hass)
    with patch(IMAP, new=fake), pytest.raises(HomeAssistantError):
        await _press(hass)
    await hass.async_block_till_done()

    assert hass.states.get(RESULT).state == "gmail_auth_failed"
    assert PASSWORD not in caplog.text


async def test_result_restored_after_restart(hass: HomeAssistant) -> None:
    mock_restore_cache(
        hass,
        [
            State(
                RESULT,
                "gmail_unavailable",
                {"detail": "connexion : socket.gaierror", "tested_at": "2026-11-08T19:00:54+00:00"},
            )
        ],
    )
    await _setup(hass)

    state = hass.states.get(RESULT)
    assert state.state == "gmail_unavailable"
    assert state.attributes["detail"] == "connexion : socket.gaierror"
    assert state.attributes["tested_at"] == "2026-11-08T19:00:54+00:00"
