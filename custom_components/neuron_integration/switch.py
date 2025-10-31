import logging
from functools import cached_property
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
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

    LOG.info("Setting up Neuron switch platform")

    data = neuron_data(hass)
    data.add_switch_entities = async_add_entities


class AutomationEnabledSwitch(SwitchEntity):
    def __init__(
        self,
        hass: HomeAssistant,
        automation: bus.Automation,
    ) -> None:
        self.hass = hass
        self.automation = automation

        # Generic properties: https://developers.home-assistant.io/docs/core/entity#generic-properties
        self.should_poll = False

        self.entity_id = f"switch.neuron_{automation.name}_enabled"
        self.name = "Enabled"
        self._attr_is_on = automation.enabled

    #
    # Registry properties: https://developers.home-assistant.io/docs/core/entity#registry-properties
    #

    @cached_property
    def unique_id(self):
        if self.automation:
            return f"neuron_sensor_{self.automation.name}_{self.name}"
        else:
            return f"neuron_sensor_{self.name}"

    @cached_property
    def device_info(self):
        return DeviceInfo(
            connections={(DOMAIN, f"neuron_{self.automation.name}")},
            default_name=f"Neuron automation: {self.automation.name}",
            default_manufacturer="Neuron",
            default_model="Automation",
            via_device=(DOMAIN, "neuron"),
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        LOG.info("Enabling automation %r", self.automation.name)
        self.is_on = True
        self.schedule_update_ha_state()

        self.hass.bus.async_fire(
            "neuron",
            bus.UpdateAutomation(
                automation=self.automation.module_name, enabled=True
            ).model_dump(),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        LOG.info("Disabling automation %r", self.automation.name)
        self.is_on = False
        self.schedule_update_ha_state()

        self.hass.bus.async_fire(
            "neuron",
            bus.UpdateAutomation(
                automation=self.automation.module_name, enabled=False
            ).model_dump(),
        )

    async def async_update(self):
        LOG.info("Updating 'automation enabled' switch %r", self)
