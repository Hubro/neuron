import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import bus
from .util import automation_device_info, neuron_context, neuron_device_info

LOG = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    del config

    LOG.info("Setting up Neuron binary sensor platform")

    data = neuron_context(hass)
    data.add_binary_sensor_entities = async_add_entities
    data.platforms_initialized.add("binary_sensor")


class ManagedBinarySensor(BinarySensorEntity):
    def __init__(
        self,
        hass: HomeAssistant,
        automation: str | None,
        unique_id: str,
        value: bool | None,
        friendly_name: str | None,
        device_class: BinarySensorDeviceClass | None,
        icon: str | None = None,
    ) -> None:
        self.hass = hass
        self.automation = automation
        self.unique_id = unique_id
        self.entity_id = unique_id
        self.name = friendly_name
        self._attr_icon = icon
        self.should_poll = False

        self._attr_is_on = value
        self._attr_device_class = device_class

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
                assert message.value is None or isinstance(message.value, bool)

                if self._attr_is_on is not message.value:
                    LOG.info(
                        "Managed binary sensor %r value set to %r from Neuron",
                        self.unique_id,
                        message.value,
                    )
                    self._attr_is_on = message.value
                    self.schedule_update_ha_state()

            case bus.FullUpdate():
                for binary_sensor_entity in message.managed_binary_sensors:
                    if binary_sensor_entity.unique_id == self.unique_id:
                        if (
                            self._attr_is_on is not binary_sensor_entity.value
                            or self.icon != binary_sensor_entity.icon
                        ):
                            self._attr_is_on = binary_sensor_entity.value
                            self._attr_icon = binary_sensor_entity.icon
                            LOG.info(
                                "Managed binary sensor %r value set to %r from Neuron full update",
                                self.unique_id,
                                binary_sensor_entity.value,
                            )
                            self.schedule_update_ha_state()
