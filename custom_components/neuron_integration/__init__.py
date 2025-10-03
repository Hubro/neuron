import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant

from neuron_integration import bus
from neuron_integration.const import DOMAIN

__all__ = ["DOMAIN", "async_setup_entry", "async_unload_entry"]

LOG = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config: ConfigEntry):
    LOG.info("Setting up Neuron integration with config: %r", config.as_dict())

    hass.bus.async_listen("neuron", _handle_event)

    hass.bus.async_fire("neuron", {})

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    LOG.info("Unloading Neuron integration entry: %r", entry.as_dict())

    return True


async def _handle_event(event: Event):
    LOG.info("Received event: %r", event.as_dict())
    bus.parse_message(event.data)
