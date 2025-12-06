"""Messages types used to communicate between addon and companion integration

This module is symlinked into the integration and dereferenced when installing.
That way they share the exact same code for message parsing.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Mapping, TypeAlias

from pydantic import BaseModel, Field, RootModel

try:
    # Neuron addon:
    from neuron.api import SensorDeviceClass, SensorStateClass
except ImportError:
    # Neuron integration:
    from homeassistant.components.sensor.const import (
        SensorDeviceClass,
        SensorStateClass,
    )

SensorValue: TypeAlias = str | int | float | date | datetime | Decimal


class BaseMessage(BaseModel):
    pass


class NeuronCoreMessage(BaseMessage):
    source: Literal["neuron-core"] = "neuron-core"


class NeuronIntegrationMessage(BaseMessage):
    source: Literal["neuron-integration"] = "neuron-integration"


#
# Messages from integration
#


class RequestingFullUpdate(NeuronIntegrationMessage):
    """Requests a full dump of Sedrec's managed entity state"""

    type: Literal["requesting-full-update"] = "requesting-full-update"


class SwitchTurnedOn(NeuronIntegrationMessage):
    """Informs Neuron that a managed switch was turned on"""

    type: Literal["switch-turned-on"] = "switch-turned-on"

    unique_id: str


class SwitchTurnedOff(NeuronIntegrationMessage):
    """Informs Neuron that a managed switch was turned off"""

    type: Literal["switch-turned-off"] = "switch-turned-off"

    unique_id: str


class ButtonPressed(NeuronIntegrationMessage):
    """Informs Neuron that a managed button was pressed"""

    type: Literal["button-pressed"] = "button-pressed"

    unique_id: str


#
# Messages from Neuron core
#


class FullUpdate(NeuronCoreMessage):
    """A full update of managed entities

    The integration can assume that any entities that aren't mentioned in
    this message can be disowned or deleted.
    """

    type: Literal["full-update"] = "full-update"
    managed_switches: list[ManagedSwitch]
    managed_sensors: list[ManagedSensor]
    managed_buttons: list[ManagedButton]


class SetValue(NeuronCoreMessage):
    """Tell the integration to set the value of a managed entity"""

    type: Literal["set-value"] = "set-value"
    unique_id: str
    value: Any

    # TODO: Attributes


class ManagedSwitch(BaseModel):
    unique_id: str
    friendly_name: str | None
    automation: str | None  # None means that the entity is managed by Neuron core
    value: bool
    # attributes: dict[str, str]


class ManagedSensor(BaseModel):
    unique_id: str
    friendly_name: str | None
    value: SensorValue
    # attributes: dict[str, str]
    automation: str | None  # None means that the entity is managed by Neuron core
    device_class: SensorDeviceClass | None
    state_class: SensorStateClass | None
    native_unit_of_measurement: str | None
    suggested_unit_of_measurement: str | None


class ManagedButton(BaseModel):
    unique_id: str
    friendly_name: str | None
    automation: str | None  # None means that the entity is managed by Neuron core


#
# /
#


Message = (
    RequestingFullUpdate
    | SwitchTurnedOn
    | SwitchTurnedOff
    | ButtonPressed
    | FullUpdate
    | SetValue
)


def parse_message(msg: Mapping[str, Any]) -> Message:
    return RootModel[
        Annotated[
            Message,
            Field(discriminator="type"),
        ]
    ](msg).root  # type: ignore
