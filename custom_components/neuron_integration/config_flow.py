import logging
from dataclasses import asdict
from typing import Any

from homeassistant import config_entries
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .const import DOMAIN

LOG = logging.getLogger(__name__)


class NeuronConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 0
    MINOR_VERSION = 0

    async def async_step_hassio(
        self, discovery_info: HassioServiceInfo
    ) -> config_entries.ConfigFlowResult:
        LOG.info(
            "Setting up Neuron config entry from supervisor API: %r",
            asdict(discovery_info),
        )

        await self.async_set_unique_id("neuron")
        self._abort_if_unique_id_configured()  # Abort setup if entry already exists

        return self.async_create_entry(title="Neuron", data=discovery_info.config)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        await self.async_set_unique_id("neuron")
        self._abort_if_unique_id_configured(
            error="Neutron integration is already configured"
        )  # Abort setup if entry already exists

        return self.async_create_entry(title="neuron", data={})
