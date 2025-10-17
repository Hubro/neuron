import logging
from functools import cached_property
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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

    LOG.info("Setting up Neuron sensor platform")

    data: NeuronIntegrationData = hass.data[DOMAIN]
    data.add_sensor_entities = async_add_entities


class NeuronSensor(SensorEntity):
    def __init__(
        self,
        hass: HomeAssistant,
        automation: str | None,
        name: str,
        friendly_name: str | None = None,
        state: Any | None = None,
    ) -> None:
        self.hass = hass
        self.automation = automation

        # Generic properties: https://developers.home-assistant.io/docs/core/entity#generic-properties
        self.should_poll = False
        self.entity_category = EntityCategory.DIAGNOSTIC

        if automation:
            self.entity_id = f"sensor.neuron_{automation}_{name}"
            self.name = (
                friendly_name
                or f"Neuron - {make_friendly(automation)} - {make_friendly(name)}"
            )
        else:
            self.entity_id = f"sensor.neuron_core_{name}"
            self.name = friendly_name or f"Neuron - {make_friendly(name)}"

        if state is None:
            self._attr_available = False
        else:
            self._attr_available = True

        self._attr_native_value = state

    #
    # Registry properties: https://developers.home-assistant.io/docs/core/entity#registry-properties
    #

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

    async def async_update(self):
        LOG.info("Updating sensor %r", self)


def make_friendly(str) -> str:
    return str.replace("_", " ").capitalize()
