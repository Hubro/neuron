import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN  # noqa

LOG = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config: ConfigEntry):
    LOG.info("Setting up Neuron integration with config: %r", config.as_dict())

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    LOG.info("Unloading Neuron integration entry: %r", entry.as_dict())

    return True
