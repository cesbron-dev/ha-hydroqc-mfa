"""Notifications d'échec : persistante et push."""

from __future__ import annotations

import logging

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant

from .const import REASON_MESSAGES

_LOGGER = logging.getLogger(__name__)

NOTIFICATION_ID = "hydroqc_mfa_failure"
TITLE = "Hydro-Québec : réauthentification à faire"
LINK = "/config/integrations/integration/hydroqc"


async def async_notify_failure(
    hass: HomeAssistant, notify_service: str | None, reason: str
) -> None:
    """Alerte Olivier : notification persistante et push optionnel."""
    text = REASON_MESSAGES.get(reason, REASON_MESSAGES["unexpected"])
    message = f"{text}. Termine la réauthentification à la main : {LINK}"
    persistent_notification.async_create(
        hass, message, title=TITLE, notification_id=NOTIFICATION_ID
    )
    if not notify_service:
        return
    domain, _, service = notify_service.partition(".")
    try:
        await hass.services.async_call(
            domain,
            service,
            {"title": TITLE, "message": message, "data": {"url": LINK}},
            blocking=True,
        )
    except Exception:  # noqa: BLE001 - jamais de détail dans les journaux
        _LOGGER.warning("Notification push impossible")


async def async_clear_failure(hass: HomeAssistant) -> None:
    """Retire la notification persistante."""
    persistent_notification.async_dismiss(hass, NOTIFICATION_ID)
