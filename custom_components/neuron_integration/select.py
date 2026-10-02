import logging

from homeassistant.components.select import SelectEntity
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
    data.add_select_entities = async_add_entities
    data.platforms_initialized.add("select")


class ManagedSelect(SelectEntity):
    def __init__(self, hass: HomeAssistant, select: bus.ManagedSelect):
        self.hass = hass
        self._attr_unique_id = select.unique_id
        self.entity_id = select.unique_id
        self._attr_should_poll = False
        self._attr_device_info = (
            automation_device_info(select.automation)
            if select.automation
            else neuron_device_info()
        )
        self._apply_update(select)

    def _apply_update(self, select: bus.ManagedSelect):
        self._attr_name = select.friendly_name
        self._attr_current_option = select.value
        self._attr_options = select.options

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
                if self.current_option != message.value:
                    self._attr_current_option = message.value
                    self.async_write_ha_state()

            case bus.FullUpdate():
                for select in message.managed_selects:
                    if select.unique_id == self.unique_id:
                        self._apply_update(select)
                        self.async_write_ha_state()
                        break

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise ValueError(f"Option must be one of {self.options!r}, got {option!r}")
        if self.current_option == option:
            return

        self._attr_current_option = option
        self.async_write_ha_state()
        send_message(
            self.hass, bus.SelectValueChanged(unique_id=self.unique_id, value=option)
        )
