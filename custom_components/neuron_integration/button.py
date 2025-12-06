import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import bus
from .util import automation_device_info, neuron_data, neuron_device_info, send_message

LOG = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    del config

    LOG.info("Setting up Neuron button platform")
    LOG.info(f"{async_add_entities=!r} {async_add_entities.__name__=!r}")  # pyright: ignore[reportAttributeAccessIssue]

    data = neuron_data(hass)
    data.add_button_entities = async_add_entities


class ManagedButton(ButtonEntity):
    """A button managed by a Neuron"""

    def __init__(
        self,
        hass: HomeAssistant,
        automation: str | None,
        unique_id: str,
        friendly_name: str | None,
    ) -> None:
        self.hass = hass
        self.automation = automation
        self.unique_id = unique_id
        self.entity_id = unique_id
        self.name = friendly_name

        # Registry properties: https://developers.home-assistant.io/docs/core/entity#registry-properties
        if self.automation:
            self.device_info = automation_device_info(self.automation)
        else:
            self.device_info = neuron_device_info()

    async def async_press(self) -> None:
        assert self.unique_id

        LOG.info("Pressing managed button: %r", self.unique_id)
        send_message(self.hass, bus.ButtonPressed(unique_id=self.unique_id))
