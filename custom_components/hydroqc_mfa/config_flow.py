"""Flux de configuration, d'options et de reconfiguration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_GMAIL_APP_PASSWORD,
    CONF_GMAIL_USERNAME,
    CONF_NOTIFY_SERVICE,
    CONF_PAUSED,
    CONF_TRASH,
    DOMAIN,
)
from .gmail import GmailError, validate_gmail

NOTIFY_PREFIX = "notify."
MOBILE_APP_PREFIX = "mobile_app_"


def _credentials_schema(username: str | None = None) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_GMAIL_USERNAME, default=username or vol.UNDEFINED): (
                TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL))
            ),
            vol.Required(CONF_GMAIL_APP_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
        }
    )


class HydroqcMfaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configuration de la connexion Gmail (une seule entrée)."""

    VERSION = 1

    async def _validate(self, username: str, password: str) -> str | None:
        """Retourne une clé d'erreur, ou None si Gmail accepte les identifiants."""
        try:
            await self.hass.async_add_executor_job(validate_gmail, username, password)
        except GmailError as err:
            return (
                err.reason
                if err.reason in ("gmail_auth_failed", "gmail_unavailable")
                else "gmail_unavailable"
            )
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._validate(
                user_input[CONF_GMAIL_USERNAME], user_input[CONF_GMAIL_APP_PASSWORD]
            )
            if error is None:
                return self.async_create_entry(
                    title="Hydro-Québec MFA",
                    data={
                        CONF_GMAIL_USERNAME: user_input[CONF_GMAIL_USERNAME],
                        CONF_GMAIL_APP_PASSWORD: user_input[CONF_GMAIL_APP_PASSWORD],
                    },
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=_credentials_schema(
                user_input.get(CONF_GMAIL_USERNAME) if user_input else None
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._validate(
                user_input[CONF_GMAIL_USERNAME], user_input[CONF_GMAIL_APP_PASSWORD]
            )
            if error is None:
                # Le listener de mise à jour (voir __init__) recharge l'entrée.
                return self.async_update_and_abort(
                    entry,
                    data_updates={
                        CONF_GMAIL_USERNAME: user_input[CONF_GMAIL_USERNAME],
                        CONF_GMAIL_APP_PASSWORD: user_input[CONF_GMAIL_APP_PASSWORD],
                    },
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_credentials_schema(
                (user_input or entry.data).get(CONF_GMAIL_USERNAME)
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HydroqcMfaOptionsFlow()


class HydroqcMfaOptionsFlow(OptionsFlow):
    """Service de notification, corbeille et pause."""

    async def async_step_init(
        self, user_input: Mapping[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            data: dict[str, Any] = {
                CONF_TRASH: user_input[CONF_TRASH],
                CONF_PAUSED: user_input[CONF_PAUSED],
            }
            if service := user_input.get(CONF_NOTIFY_SERVICE):
                data[CONF_NOTIFY_SERVICE] = f"{NOTIFY_PREFIX}{service}"
            return self.async_create_entry(data=data)

        options = self.config_entry.options
        current = options.get(CONF_NOTIFY_SERVICE) or ""
        current = current.removeprefix(NOTIFY_PREFIX)
        services = {
            name
            for name in self.hass.services.async_services_for_domain("notify")
            if name.startswith(MOBILE_APP_PREFIX)
        }
        if current:
            services.add(current)
        schema = vol.Schema(
            {
                vol.Optional(CONF_NOTIFY_SERVICE): SelectSelector(
                    SelectSelectorConfig(
                        options=sorted(services),
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_TRASH, default=options.get(CONF_TRASH, True)
                ): BooleanSelector(),
                vol.Required(
                    CONF_PAUSED, default=options.get(CONF_PAUSED, False)
                ): BooleanSelector(),
            }
        )
        if current:
            schema = self.add_suggested_values_to_schema(
                schema, {CONF_NOTIFY_SERVICE: current}
            )
        return self.async_show_form(step_id="init", data_schema=schema)
