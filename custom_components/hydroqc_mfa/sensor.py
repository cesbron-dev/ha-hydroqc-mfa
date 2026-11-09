"""Capteurs de suivi des renouvellements."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import HydroqcMfaConfigEntry
from .const import (
    DOMAIN,
    GMAIL_TEST_NO_HQ_MAIL,
    GMAIL_TEST_OK,
    REASON_MESSAGES,
    SIGNAL_GMAIL_TEST,
    SIGNAL_RESULT,
)
from .gmail import HqMailSurvey

SUCCESS = "success"
RESULT_OPTIONS = [SUCCESS, *REASON_MESSAGES]


class _HydroqcMfaSensor(SensorEntity, RestoreEntity):
    """Base : écoute `SIGNAL_RESULT` et restaure l'état au redémarrage."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry_id: str, key: str, object_id: str) -> None:
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_translation_key = key
        # Les identifiants d'entité restent en français quelle que soit la langue.
        self.entity_id = f"sensor.{object_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Hydroqc MFA",
            manufacturer="cesbron-dev",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._restore(last.state)
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_RESULT, self._handle_result)
        )

    def _restore(self, state: str) -> None:
        raise NotImplementedError

    @callback
    def _handle_result(self, reason: str, when: datetime) -> None:
        raise NotImplementedError


class LastRenewalSensor(_HydroqcMfaSensor):
    """Horodatage du dernier renouvellement réussi."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry_id: str) -> None:
        super().__init__(entry_id, "last_renewal", "hydroqc_mfa_dernier_renouvellement")

    def _restore(self, state: str) -> None:
        parsed = dt_util.parse_datetime(state)
        self._attr_native_value = dt_util.as_utc(parsed) if parsed else None

    @callback
    def _handle_result(self, reason: str, when: datetime) -> None:
        if reason == SUCCESS:
            self._attr_native_value = when
            self.async_write_ha_state()


class LastAttemptSensor(_HydroqcMfaSensor):
    """Résultat de la dernière tentative (succès ou raison d'échec)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = RESULT_OPTIONS

    def __init__(self, entry_id: str) -> None:
        super().__init__(entry_id, "last_attempt", "hydroqc_mfa_derniere_tentative")

    def _restore(self, state: str) -> None:
        if state in RESULT_OPTIONS:
            self._attr_native_value = state

    @callback
    def _handle_result(self, reason: str, when: datetime) -> None:
        self._attr_native_value = reason if reason in RESULT_OPTIONS else "unexpected"
        self.async_write_ha_state()


GMAIL_TEST_OPTIONS = [
    GMAIL_TEST_OK,
    GMAIL_TEST_NO_HQ_MAIL,
    "gmail_unavailable",
    "gmail_auth_failed",
    "unexpected",
]
GMAIL_TEST_ATTRIBUTES = ("detail", "tested_at", "hq_messages", "hq_last_received")


class GmailTestSensor(SensorEntity, RestoreEntity):
    """Résultat du dernier appui sur « Tester la connexion Gmail »."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "gmail_test"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = GMAIL_TEST_OPTIONS
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry_id: str) -> None:
        self._attr_unique_id = f"{entry_id}_gmail_test"
        self.entity_id = "sensor.hydroqc_mfa_test_gmail"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Hydroqc MFA",
            manufacturer="cesbron-dev",
        )
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in GMAIL_TEST_OPTIONS:
            self._attr_native_value = last.state
            self._attr_extra_state_attributes = {
                key: last.attributes.get(key) for key in GMAIL_TEST_ATTRIBUTES
            }
            self._attr_extra_state_attributes["detail"] = last.attributes.get("detail", "")
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_GMAIL_TEST, self._handle_test)
        )

    @callback
    def _handle_test(
        self, result: str, detail: str, when: datetime, survey: HqMailSurvey | None
    ) -> None:
        self._attr_native_value = result if result in GMAIL_TEST_OPTIONS else "unexpected"
        last = survey.hq_last_received if survey else None
        self._attr_extra_state_attributes = {
            "detail": detail,
            "tested_at": when.isoformat(),
            "hq_messages": survey.hq_messages if survey else None,
            "hq_last_received": last.isoformat() if last else None,
        }
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant, entry: HydroqcMfaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Crée les capteurs de suivi et celui du test Gmail."""
    async_add_entities(
        [
            LastRenewalSensor(entry.entry_id),
            LastAttemptSensor(entry.entry_id),
            GmailTestSensor(entry.entry_id),
        ]
    )
