"""Faux flux de configuration `hydroqc` (contrat de hydroqc-ha v0.9.0)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_integration,
    mock_platform,
)

HQ_PASSWORD = "hq-pass"
HQ_CODE = "123456"
HTTPCLIENT_LOGGER = logging.getLogger("hydroqc.httpclient")


@dataclass
class HydroBehavior:
    """Comportement paramétrable du faux flux et compteurs d'appels."""

    password: str = HQ_PASSWORD
    password_error: str = "invalid_auth"
    code: str = HQ_CODE
    abort_after_password: bool = False
    unexpected_step_after_password: bool = False
    raise_on_password: bool = False
    on_password: Callable[[], None] | None = None
    passwords: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    reauth_calls: int = 0


class FakeHydroFlow(ConfigFlow):
    """reauth → reauth_confirm (password) → mfa (mfa_code) → abort reauth_successful."""

    VERSION = 1
    behavior = HydroBehavior()

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self.behavior.reauth_calls += 1
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        schema = vol.Schema({vol.Required("password"): str})
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm", data_schema=schema)
        behavior = self.behavior
        behavior.passwords.append(user_input["password"])
        # hydroqc-ha est verbeux : la compagnon doit filtrer ce logger pendant la tentative.
        HTTPCLIENT_LOGGER.debug("POST https://hq.example/login?pw=%s", user_input["password"])
        if behavior.on_password is not None:
            behavior.on_password()
        if behavior.raise_on_password:
            raise RuntimeError(f"échec réseau {user_input['password']}")
        if user_input["password"] != behavior.password:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=schema,
                errors={"base": behavior.password_error},
            )
        if behavior.abort_after_password:
            return self.async_abort(reason="reauth_successful")
        if behavior.unexpected_step_after_password:
            return self.async_show_form(step_id="contract", data_schema=vol.Schema({}))
        return await self.async_step_mfa()

    async def async_step_contract(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_form(step_id="contract", data_schema=vol.Schema({}))

    async def async_step_mfa(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        schema = vol.Schema({vol.Required("mfa_code"): str})
        if user_input is None:
            return self.async_show_form(step_id="mfa", data_schema=schema)
        self.behavior.codes.append(user_input["mfa_code"])
        if user_input["mfa_code"] != self.behavior.code:
            return self.async_show_form(
                step_id="mfa", data_schema=schema, errors={"base": "invalid_mfa_code"}
            )
        return self.async_abort(reason="reauth_successful")


def register_fake_hydroqc(hass: HomeAssistant) -> None:
    """Déclare l'intégration `hydroqc` factice (à combiner avec `mock_config_flow`)."""
    mock_integration(hass, MockModule("hydroqc"))
    mock_platform(hass, "hydroqc.config_flow", None)


def hydro_entry(hass: HomeAssistant, data: dict[str, Any] | None = None) -> MockConfigEntry:
    """Ajoute une entrée hydroqc portant le mot de passe Hydro-Québec."""
    entry = MockConfigEntry(
        domain="hydroqc",
        title="Maison",
        data=data if data is not None else {"username": "olivier@example.com", "password": HQ_PASSWORD},
    )
    entry.add_to_hass(hass)
    return entry


async def async_open_reauth(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """Ouvre le flux de réauthentification et renvoie son `flow_id`."""
    entry.async_start_reauth(hass)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(
        "hydroqc", match_context={"source": "reauth"}
    )
    return next(flow["flow_id"] for flow in flows if flow["context"]["entry_id"] == entry.entry_id)
