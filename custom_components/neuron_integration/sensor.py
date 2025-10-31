import logging
from functools import cached_property
from typing import Any, Literal

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.device_registry import (
    DeviceInfo,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import bus
from .const import DOMAIN
from .util import neuron_data

LOG = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    del config

    LOG.info("Setting up Neuron sensor platform")

    data = neuron_data(hass)
    data.add_sensor_entities = async_add_entities


class NeuronStatsSensor(SensorEntity):
    def __init__(
        self,
        hass: HomeAssistant,
        stat: Literal[
            "trigger_subscriptions", "event_subscriptions", "state_subscriptions"
        ],
        state: Any | None = None,
    ) -> None:
        self.hass = hass
        self.stat = stat
        self.stop_event_listener = None

        # Generic properties: https://developers.home-assistant.io/docs/core/entity#generic-properties
        self.should_poll = False
        self.entity_category = EntityCategory.DIAGNOSTIC

        self.entity_id = f"sensor.neuron_core_stats_{stat}"
        self.name = f"Neuron {make_friendly(stat)}"

        self._attr_available = state is not None
        self._attr_native_value = state

    async def async_added_to_hass(self) -> None:
        self.stop_event_listener = self.hass.bus.async_listen(
            "neuron", self.event_listener
        )

    async def async_will_remove_from_hass(self) -> None:
        if self.stop_event_listener:
            self.stop_event_listener()

    async def event_listener(self, event: Event):
        try:
            message = bus.parse_message(event.data)
        except Exception:
            LOG.exception("Failed to parse message from Neuron")
            return

        if isinstance(message, (bus.StatsUpdate, bus.FullUpdate)):
            stat = getattr(message, self.stat)

            if stat != self._attr_native_value:
                self._attr_native_value = stat
                self.schedule_update_ha_state()

    #
    # Registry properties: https://developers.home-assistant.io/docs/core/entity#registry-properties
    #

    @cached_property
    def unique_id(self):
        return f"sensor.neuron_core_stats_{self.stat}"

    @cached_property
    def device_info(self):
        return DeviceInfo(
            configuration_url=None,
            connections=set(),
            entry_type=None,
            hw_version=None,
            identifiers={(DOMAIN, "neuron")},
            manufacturer=None,
            model="Neuron core",
            name="Neuron",
            suggested_area=None,
            sw_version=None,
            via_device=None,  # type: ignore
        )

    async def async_update(self):
        LOG.info("Updating neuron stats sensor %r", self)


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
        else:
            self.entity_id = f"sensor.neuron_core_{name}"

        self.name = friendly_name or make_friendly(name)

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
