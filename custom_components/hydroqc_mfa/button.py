"""Bouton de diagnostic : tester la connexion Gmail sans rien demander à Hydro-Québec."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HydroqcMfaConfigEntry
from .const import (
    CONF_GMAIL_APP_PASSWORD,
    CONF_GMAIL_USERNAME,
    DOMAIN,
    GMAIL_TEST_OK,
    REASON_MESSAGES,
    SIGNAL_GMAIL_TEST,
)
from .gmail import GmailError, validate_gmail

_LOGGER = logging.getLogger(__name__)


class TestGmailButton(ButtonEntity):
    """Se connecte à Gmail et ouvre INBOX, comme à la création ; ne lit aucun message.

    N'agit ni sur le verrou anti-relance ni sur les capteurs de renouvellement : le
    résultat va au capteur « Test Gmail ».
    """

    _attr_has_entity_name = True
    _attr_translation_key = "test_gmail"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: HydroqcMfaConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_test_gmail"
        self.entity_id = "button.hydroqc_mfa_tester_gmail"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Hydroqc MFA",
            manufacturer="cesbron-dev",
        )

    async def async_press(self) -> None:
        username = self._entry.data[CONF_GMAIL_USERNAME]
        password = self._entry.data[CONF_GMAIL_APP_PASSWORD]
        try:
            await self.hass.async_add_executor_job(validate_gmail, username, password)
        except GmailError as err:
            reason, detail = err.reason, err.detail
        except Exception as err:  # noqa: BLE001 - type seulement : le message peut contenir un secret
            reason, detail = "unexpected", type(err).__name__
        else:
            async_dispatcher_send(self.hass, SIGNAL_GMAIL_TEST, GMAIL_TEST_OK, "", dt_util.utcnow())
            return
        _LOGGER.warning(
            "Test de connexion Gmail en échec (%s ; %s)", reason, detail or "sans détail"
        )
        async_dispatcher_send(self.hass, SIGNAL_GMAIL_TEST, reason, detail, dt_util.utcnow())
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="gmail_test_failed",
            translation_placeholders={
                "reason": REASON_MESSAGES.get(reason, reason),
                "detail": detail or "sans détail",
            },
        )


async def async_setup_entry(
    hass: HomeAssistant, entry: HydroqcMfaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Crée le bouton de test."""
    async_add_entities([TestGmailButton(entry)])
