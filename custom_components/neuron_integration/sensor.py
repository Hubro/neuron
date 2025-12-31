import logging
from datetime import date, datetime
from decimal import Decimal

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import bus
from .util import automation_device_info, neuron_context, neuron_device_info

LOG = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    del config

    LOG.info("Setting up Neuron sensor platform")

    data = neuron_context(hass)
    data.add_sensor_entities = async_add_entities
    data.platforms_initialized.add("sensor")


class ManagedSensor(SensorEntity):
    def __init__(
        self,
        hass: HomeAssistant,
        automation: str | None,
        unique_id: str,
        value: StateType | datetime | date | Decimal,
        friendly_name: str | None,
    ) -> None:
        self.hass = hass
        self.automation = automation
        self.unique_id = unique_id
        self.entity_id = unique_id

        # Generic properties: https://developers.home-assistant.io/docs/core/entity#generic-properties
        self.name = friendly_name
        self.should_poll = False

        self._attr_native_value = value

        # Registry properties: https://developers.home-assistant.io/docs/core/entity#registry-properties
        if self.automation:
            self.device_info = automation_device_info(self.automation)
        else:
            self.device_info = neuron_device_info()

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

        match message:
            case bus.SetValue(unique_id=self.unique_id):
                if self.native_value is not message.value:
                    LOG.info(
                        "Managed sensor %r value set to %r from Neuron",
                        self.unique_id,
                        message.value,
                    )
                    self._attr_native_value = message.value
                    self.schedule_update_ha_state()

            case bus.FullUpdate():
                for sensor_entity in message.managed_sensors:
                    if sensor_entity.unique_id == self.unique_id:
                        if self._attr_native_value is not sensor_entity.value:
                            self._attr_native_value = sensor_entity.value
                            LOG.info(
                                "Managed sensor %r value set to %r from Neuron full update",
                                self.unique_id,
                                sensor_entity.value,
                            )
                            self.schedule_update_ha_state()

    async def async_update(self):
        LOG.info("Updating sensor %r", self)
