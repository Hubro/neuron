import logging

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import bus
from .util import (
    automation_device_info,
    neuron_context,
    neuron_device_info,
    send_message,
)

LOG = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    del config

    data = neuron_context(hass)
    data.add_number_entities = async_add_entities
    data.platforms_initialized.add("number")


class ManagedNumber(NumberEntity):
    def __init__(self, hass: HomeAssistant, number: bus.ManagedNumber):
        self.hass = hass
        self._attr_unique_id = number.unique_id
        self.entity_id = number.unique_id
        self._attr_should_poll = False
        self._attr_device_info = (
            automation_device_info(number.automation)
            if number.automation
            else neuron_device_info()
        )
        self._apply_update(number)

    def _apply_update(self, number: bus.ManagedNumber):
        self._attr_name = number.friendly_name
        self._attr_icon = number.icon
        self._attr_native_value = number.value
        self._attr_native_min_value = number.min_value
        self._attr_native_max_value = number.max_value
        self._attr_native_step = number.step
        self._attr_native_unit_of_measurement = number.unit_of_measurement
        self._attr_device_class = number.device_class
        self._attr_mode = number.mode

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.hass.bus.async_listen("neuron", self.event_listener))

    async def event_listener(self, event: Event):
        try:
            message = bus.parse_message(event.data)
        except Exception:
            LOG.exception("Failed to parse message from Neuron")
            return

        match message:
            case bus.SetValue(unique_id=self.unique_id):
                if self.native_value != message.value:
                    self._attr_native_value = message.value
                    self.async_write_ha_state()

            case bus.FullUpdate():
                for number in message.managed_numbers:
                    if number.unique_id == self.unique_id:
                        self._apply_update(number)
                        self.async_write_ha_state()
                        break

    async def async_set_native_value(self, value: float) -> None:
        message = bus.NumberValueChanged(unique_id=self.unique_id, value=value)
        if not self.native_min_value <= value <= self.native_max_value:
            raise ValueError(
                f"Value must be between {self.native_min_value} and {self.native_max_value}"
            )
        if self.native_value == value:
            return

        self._attr_native_value = value
        self.async_write_ha_state()
        send_message(self.hass, message)
