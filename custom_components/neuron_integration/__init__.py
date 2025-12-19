import asyncio
import logging
from functools import partial
from typing import assert_never

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from . import bus
from .button import ManagedButton
from .const import DOMAIN
from .sensor import ManagedSensor
from .switch import ManagedSwitch
from .util import neuron_data, neuron_device_info, send_message

__all__ = ["DOMAIN", "async_setup_entry", "async_unload_entry"]

LOG = logging.getLogger(__name__)
PLATFORMS = ["switch", "sensor", "button"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    LOG.info("Setting up Neuron integration")

    setup_neuron_device(hass, entry)

    data = neuron_data(hass)

    if platforms := set(PLATFORMS) - data.platforms_initialized:
        await hass.config_entries.async_forward_entry_setups(entry, platforms)

    data.cleanup_event_listener = hass.bus.async_listen(
        "neuron",
        partial(_handle_event, hass),
    )

    try:
        for _ in range(10):
            LOG.info("Sending RequestingFullUpdate message")
            send_message(hass, bus.RequestingFullUpdate())

            await asyncio.sleep(1)

            if data.entities_created:
                break

        if not data.entities_created:
            raise ConfigEntryNotReady("No response from Neuron, try again soon")
    except Exception:
        data.cleanup_event_listener()
        raise

    LOG.info("Neuron integration setup complete")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    LOG.info("Unloading Neuron integration entry: %r", entry.as_dict())

    data = neuron_data(hass)

    if data.cleanup_event_listener:
        LOG.info("Cleaning up event listener")
        data.cleanup_event_listener()

    await hass.config_entries.async_unload_platforms(
        entry, ["switch", "sensor", "button"]
    )

    return True


async def _handle_event(hass: HomeAssistant, event: Event):
    LOG.debug("Received event: %r", event.as_dict())

    try:
        message = bus.parse_message(event.data)
    except Exception:
        LOG.exception("Failed to parse message from Neuron")
        return

    if isinstance(message, bus.NeuronIntegrationMessage):
        return  # Ignore our own messages

    match message:
        case bus.FullUpdate():
            LOG.info("Received full state update from Neuron: %r", message)
            setup_entities(hass, message)

        case bus.SetValue():
            pass  # Handled by each entity

        case other:
            assert_never(other)


def setup_neuron_device(hass: HomeAssistant, entry: ConfigEntry):
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, **neuron_device_info()
    )


def setup_entities(hass: HomeAssistant, message: bus.FullUpdate):
    data = neuron_data(hass)

    data.add_switches(
        ManagedSwitch(
            hass,
            automation=x.automation,
            unique_id=x.unique_id,
            value=x.value,
            friendly_name=x.friendly_name,
        )
        for x in message.managed_switches
    )

    data.add_sensors(
        ManagedSensor(
            hass,
            automation=x.automation,
            unique_id=x.unique_id,
            value=x.value,
            friendly_name=x.friendly_name,
        )
        for x in message.managed_sensors
    )

    data.add_buttons(
        ManagedButton(
            hass,
            automation=x.automation,
            unique_id=x.unique_id,
            friendly_name=x.friendly_name,
        )
        for x in message.managed_buttons
    )

    data.entities_created = True
