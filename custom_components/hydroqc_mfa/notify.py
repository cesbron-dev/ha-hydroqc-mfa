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
LINK_LABEL = "Appareils et services → Hydro-Québec"


async def async_notify_failure(
    hass: HomeAssistant, notify_service: str | None, reason: str
) -> None:
    """Alerte Olivier : notification persistante et push optionnel."""
    text = REASON_MESSAGES.get(reason, REASON_MESSAGES["unexpected"])
    persistent_notification.async_create(
        hass,
        f"{text}. Termine la réauthentification à la main : [{LINK_LABEL}]({LINK})",
        title=TITLE,
        notification_id=NOTIFICATION_ID,
    )
    if not notify_service:
        return
    domain, _, service = notify_service.partition(".")
    try:
        await hass.services.async_call(
            domain,
            service,
            {
                "title": TITLE,
                # Texte brut : le push n'affiche pas le Markdown ; le toucher ouvre LINK.
                "message": f"{text}. Termine la réauthentification à la main : {LINK}",
                # `url` pour iOS, `clickAction` pour Android.
                "data": {"url": LINK, "clickAction": LINK},
            },
            blocking=True,
        )
    except Exception:  # noqa: BLE001 - jamais de détail dans les journaux
        _LOGGER.warning("Notification push impossible")


async def async_clear_failure(hass: HomeAssistant) -> None:
    """Retire la notification persistante."""
    persistent_notification.async_dismiss(hass, NOTIFICATION_ID)
