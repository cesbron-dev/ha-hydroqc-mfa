"""Tests des notifications d'échec."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.hydroqc_mfa.const import REASON_MESSAGES
from custom_components.hydroqc_mfa.notify import (
    NOTIFICATION_ID,
    async_clear_failure,
    async_notify_failure,
)

LINK = "/config/integrations/integration/hydroqc"


def _persistent(hass: HomeAssistant) -> dict:
    return hass.data["persistent_notification"]


async def test_failure_creates_persistent_and_push(hass):
    calls = async_mock_service(hass, "notify", "mobile_app_test")
    await async_notify_failure(hass, "notify.mobile_app_test", "code_timeout")
    await hass.async_block_till_done()

    assert len(calls) == 1
    data = calls[0].data
    assert data["title"] == "Hydro-Québec : réauthentification à faire"
    assert REASON_MESSAGES["code_timeout"] in data["message"]
    assert LINK in data["message"]
    # `url` pour iOS, `clickAction` pour Android.
    assert data["data"] == {"url": LINK, "clickAction": LINK}
    assert "[" not in data["message"]

    note = _persistent(hass)[NOTIFICATION_ID]
    assert note["title"] == "Hydro-Québec : réauthentification à faire"
    assert REASON_MESSAGES["code_timeout"] in note["message"]
    assert "Termine la réauthentification à la main :" in note["message"]
    assert f"[Appareils et services → Hydro-Québec]({LINK})" in note["message"]


async def test_failure_without_push_service(hass):
    await async_notify_failure(hass, None, "gmail_unavailable")
    await hass.async_block_till_done()
    assert REASON_MESSAGES["gmail_unavailable"] in _persistent(hass)[NOTIFICATION_ID]["message"]


async def test_unknown_reason_falls_back(hass):
    await async_notify_failure(hass, None, "nimporte quoi")
    await hass.async_block_till_done()
    msg = _persistent(hass)[NOTIFICATION_ID]["message"]
    assert REASON_MESSAGES["unexpected"] in msg
    assert "nimporte" not in msg


async def test_push_failure_still_creates_persistent(hass, caplog):
    # service inexistant
    await async_notify_failure(hass, "notify.absent", "code_rejected")
    await hass.async_block_till_done()
    assert NOTIFICATION_ID in _persistent(hass)

    # service qui lève
    async def boom(call):
        raise RuntimeError("secret-detail")

    hass.services.async_register("notify", "broken", boom)
    await async_clear_failure(hass)
    await hass.async_block_till_done()
    await async_notify_failure(hass, "notify.broken", "code_rejected")
    await hass.async_block_till_done()
    assert NOTIFICATION_ID in _persistent(hass)
    assert "secret-detail" not in caplog.text


async def test_clear_removes_notification(hass):
    await async_notify_failure(hass, None, "code_timeout")
    await hass.async_block_till_done()
    assert NOTIFICATION_ID in _persistent(hass)
    await async_clear_failure(hass)
    await hass.async_block_till_done()
    assert NOTIFICATION_ID not in _persistent(hass)
