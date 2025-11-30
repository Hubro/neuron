import logging
from functools import cached_property
from typing import Any

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

    LOG.info("Setting up Neuron switch platform")

    data = neuron_data(hass)
    data.add_switch_entities = async_add_entities


class ManagedSwitch(SwitchEntity):
    """A switch managed by a Neuron"""

    def __init__(
        self,
        hass: HomeAssistant,
        automation: str | None,
        unique_id: str,
        state: str,
        friendly_name: str,
    ) -> None:
        self.hass = hass
        self.automation = automation
        self.unique_id = unique_id
        self.is_on = state == "on"
        self.name = friendly_name

        # Generic properties: https://developers.home-assistant.io/docs/core/entity#generic-properties
        self.should_poll = False

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

        if isinstance(message, bus.SetState):
            assert message.state in ["on", "off"]
            is_on = message.state == "on"

            if self.is_on is not is_on:
                self.is_on = is_on
                LOG.info(
                    "New switch state received from Neuron: %r %r",
                    self.unique_id,
                    message.state,
                )
                self.schedule_update_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.is_on:
            return

        self.is_on = True
        self.schedule_update_ha_state()
        LOG.info("Switch turned on: %r", self.unique_id)

        assert self.unique_id

        send_message(self.hass, bus.SwitchTurnedOn(unique_id=self.unique_id))

    async def async_turn_off(self, **kwargs: Any) -> None:
        if not self.is_on:
            return

        self.is_on = False
        self.schedule_update_ha_state()
        LOG.info("Switch turned off: %r", self.unique_id)

        assert self.unique_id

        send_message(self.hass, bus.SwitchTurnedOff(unique_id=self.unique_id))

    #
    # Registry properties: https://developers.home-assistant.io/docs/core/entity#registry-properties
    #

    @cached_property
    def device_info(self):
        if self.automation:
            return automation_device_info(self.automation)
        else:
            return neuron_device_info()
