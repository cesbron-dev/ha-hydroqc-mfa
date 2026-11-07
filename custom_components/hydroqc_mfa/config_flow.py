"""Config flow (squelette ; complété dans une tâche ultérieure)."""

from homeassistant import config_entries

from .const import DOMAIN


class HydroqcMfaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Flux de configuration minimal."""

    VERSION = 1
