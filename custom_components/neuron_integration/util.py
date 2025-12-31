from typing import cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from . import bus
from .const import DOMAIN
from .context import NeuronIntegrationContext


def send_message(hass: HomeAssistant, message: bus.Message):
    hass.bus.async_fire("neuron", message.model_dump())


def neuron_context(hass: HomeAssistant) -> NeuronIntegrationContext:
    return cast(
        NeuronIntegrationContext,
        hass.data.setdefault(DOMAIN, NeuronIntegrationContext(hass)),
    )


def automation_device_info(automation: str):
    return DeviceInfo(
        connections={(DOMAIN, f"neuron_{automation}")},
        default_name=f"Neuron automation: {automation}",
        default_manufacturer="Neuron",
        default_model="Automation",
        via_device=(DOMAIN, "neuron"),
    )


def neuron_device_info():
    return DeviceInfo(
        configuration_url=None,
        connections=set(),
        entry_type=None,
        hw_version=None,
        identifiers={(DOMAIN, "neuron")},
        manufacturer=None,
        model="Neuron core",
        name="Neuron",
        suggested_area=None,
        sw_version=None,
        via_device=None,  # type: ignore
    )
