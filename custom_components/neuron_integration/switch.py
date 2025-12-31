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
    data.platforms_initialized.add("switch")


class ManagedSwitch(SwitchEntity):
    """A switch managed by a Neuron"""

    def __init__(
        self,
        hass: HomeAssistant,
        automation: str | None,
        unique_id: str,
        value: bool,
        friendly_name: str | None,
    ) -> None:
        self.hass = hass
        self.automation = automation
        self.unique_id = unique_id
        self.entity_id = unique_id
        self.is_on = value
        self.name = friendly_name

        # Generic properties: https://developers.home-assistant.io/docs/core/entity#generic-properties
        self.should_poll = False

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
                assert isinstance(message.value, bool)

                if self.is_on is not message.value:
                    self.is_on = message.value
                    LOG.info(
                        "Managed switch %r value set to %r from Neuron",
                        self.unique_id,
                        message.value,
                    )
                    self.schedule_update_ha_state()

            case bus.FullUpdate():
                for switch_entity in message.managed_switches:
                    if switch_entity.unique_id == self.unique_id:
                        if self.is_on is not switch_entity.value:
                            self.is_on = switch_entity.value
                            LOG.info(
                                "Managed switch %r value set to %r from Neuron full update",
                                self.unique_id,
                                switch_entity.value,
                            )
                            self.schedule_update_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.is_on:
            return

        LOG.info("Turning on managed switch: %r", self.unique_id)

        self.is_on = True
        self.schedule_update_ha_state()

        assert self.unique_id

        send_message(self.hass, bus.SwitchTurnedOn(unique_id=self.unique_id))

    async def async_turn_off(self, **kwargs: Any) -> None:
        if not self.is_on:
            return

        LOG.info("Turning off managed switch: %r", self.unique_id)

        self.is_on = False
        self.schedule_update_ha_state()

        assert self.unique_id

        send_message(self.hass, bus.SwitchTurnedOff(unique_id=self.unique_id))
