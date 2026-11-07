"""Tests du flux de configuration, des options et de la reconfiguration."""

from unittest.mock import patch

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydroqc_mfa.const import (
    CONF_GMAIL_APP_PASSWORD,
    CONF_GMAIL_USERNAME,
    CONF_NOTIFY_SERVICE,
    CONF_PAUSED,
    CONF_TRASH,
    DOMAIN,
)
from custom_components.hydroqc_mfa.gmail import GmailError

VALIDATE = "custom_components.hydroqc_mfa.config_flow.validate_gmail"
SETUP = "custom_components.hydroqc_mfa.async_setup_entry"
USER_INPUT = {
    CONF_GMAIL_USERNAME: "olivier@example.com",
    CONF_GMAIL_APP_PASSWORD: "abcd efgh ijkl mnop",
}


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, title="Hydro-Québec MFA", data=dict(USER_INPUT)
    )


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    with patch(VALIDATE, return_value=None) as validate, patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hydro-Québec MFA"
    assert result["data"] == USER_INPUT
    validate.assert_called_once_with(
        USER_INPUT[CONF_GMAIL_USERNAME], USER_INPUT[CONF_GMAIL_APP_PASSWORD]
    )


async def test_user_flow_bad_password(hass: HomeAssistant) -> None:
    with patch(VALIDATE, side_effect=GmailError("gmail_auth_failed")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "gmail_auth_failed"}
    assert USER_INPUT[CONF_GMAIL_APP_PASSWORD] not in repr(result)


async def test_user_flow_gmail_unavailable(hass: HomeAssistant) -> None:
    with patch(VALIDATE, side_effect=GmailError("gmail_unavailable")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["errors"] == {"base": "gmail_unavailable"}


async def test_single_instance(hass: HomeAssistant) -> None:
    _entry().add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] in ("single_instance_allowed", "already_configured")


async def test_options_flow(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)

    async def _svc(call: ServiceCall) -> None:
        return None

    hass.services.async_register("notify", "mobile_app_pixel", _svc)
    hass.services.async_register("notify", "persistent_notification", _svc)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    schema = result["data_schema"].schema
    defaults = {str(k): k.default() for k in schema if k.default is not None
                and callable(k.default) and k.default() is not None}
    assert defaults.get(CONF_TRASH) is True
    assert defaults.get(CONF_PAUSED) is False

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NOTIFY_SERVICE: "mobile_app_pixel",
            CONF_TRASH: False,
            CONF_PAUSED: True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_NOTIFY_SERVICE: "notify.mobile_app_pixel",
        CONF_TRASH: False,
        CONF_PAUSED: True,
    }


async def test_options_flow_without_notify(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TRASH: True, CONF_PAUSED: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_NOTIFY_SERVICE not in result["data"]


async def test_reconfigure_updates_password(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    new = {**USER_INPUT, CONF_GMAIL_APP_PASSWORD: "wxyz wxyz wxyz wxyz"}
    with patch(VALIDATE, return_value=None), patch(SETUP, return_value=True):
        result = await entry.start_reconfigure_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reconfigure"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], new
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_GMAIL_APP_PASSWORD] == new[CONF_GMAIL_APP_PASSWORD]


async def test_reconfigure_bad_password(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(VALIDATE, side_effect=GmailError("gmail_auth_failed")):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "gmail_auth_failed"}
    assert entry.data[CONF_GMAIL_APP_PASSWORD] == USER_INPUT[CONF_GMAIL_APP_PASSWORD]
