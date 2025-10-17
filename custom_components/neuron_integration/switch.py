import logging
from functools import cached_property
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import (
    DeviceInfo,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .types import NeuronIntegrationData

LOG = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    del config

    LOG.info("Setting up Neuron switch platform")

    data: NeuronIntegrationData = hass.data[DOMAIN]
    data.add_switch_entities = async_add_entities

    async_add_entities(
        [
            NeuronSwitch(
                hass,
                automation=None,
                name="neuron_enabled",
                friendly_name="Neuron enabled",
            )
        ]
    )


class NeuronSwitch(SwitchEntity):
    def __init__(
        self,
        hass: HomeAssistant,
        automation: str | None,
        name: str,
        friendly_name: str | None = None,
    ) -> None:
        self.hass = hass
        self.automation = automation

        # Generic properties: https://developers.home-assistant.io/docs/core/entity#generic-properties
        self.should_poll = False

        if automation:
            self.entity_id = f"switch.neuron_{automation}_{name}"
            self.name = (
                friendly_name
                or f"Neuron - {make_friendly(automation)} - {make_friendly(name)}"
            )
        else:
            self.entity_id = f"switch.neuron_core_{name}"
            self.name = friendly_name or f"Neuron - {make_friendly(name)}"

        self._attr_is_on = True

    #
    # Registry properties: https://developers.home-assistant.io/docs/core/entity#registry-properties
    #

    @cached_property
    def is_on(self) -> bool | None:
        return self._attr_is_on

    @cached_property
    def unique_id(self):
        if self.automation:
            return f"neuron_sensor_{self.automation}_{self.name}"
        else:
            return f"neuron_sensor_{self.name}"

    @cached_property
    def device_info(self):
        if self.automation:
            return DeviceInfo(
                connections={(DOMAIN, f"neuron_{self.automation}")},
                default_name=f"Neuron automation: {self.automation}",
                default_manufacturer="Neuron",
                default_model="Automation",
                via_device=(DOMAIN, "neuron"),
            )

        else:
            return DeviceInfo(
                configuration_url=None,
                connections=set(),
                entry_type=None,
                hw_version=None,
                identifiers={(DOMAIN, "neuron")},
                manufacturer=None,
                model=None,
                name="Neuron",
                suggested_area=None,
                sw_version=None,
                via_device=None,  # type: ignore
            )

    def turn_on(self, **kwargs: Any) -> None:
        LOG.info("Turning switch on")
        self._attr_is_on = True
        self.schedule_update_ha_state()

    def turn_off(self, **kwargs: Any) -> None:
        LOG.info("Turning switch off")
        self._attr_is_on = False
        self.schedule_update_ha_state()

    async def async_update(self):
        LOG.info("Updating switch %r", self)


def make_friendly(str) -> str:
    return str.replace("_", " ").capitalize()
