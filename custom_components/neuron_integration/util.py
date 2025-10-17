from typing import cast
from homeassistant.core import HomeAssistant

from .types import NeuronIntegrationData
from .const import DOMAIN


def neuron_data(hass: HomeAssistant) -> NeuronIntegrationData:
    return cast(
        NeuronIntegrationData,
        hass.data.setdefault(DOMAIN, NeuronIntegrationData()),
    )
