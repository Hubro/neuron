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
    from neuron.api import (
        BinarySensorDeviceClass,
        NumberDeviceClass,
        NumberMode,
        SensorDeviceClass,
        SensorStateClass,
    )
except ImportError:
    # Neuron integration:
    from homeassistant.components.binary_sensor import BinarySensorDeviceClass
    from homeassistant.components.number import NumberDeviceClass, NumberMode
    from homeassistant.components.sensor.const import (
        SensorDeviceClass,
        SensorStateClass,
    )

SensorValue: TypeAlias = str | int | float | date | datetime | Decimal | None


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


class RequestingInternalStateDump(NeuronIntegrationMessage):
    """Requests a full dump of Sedrec's managed entity state"""

    type: Literal["requesting-internal-state-dump"] = "requesting-internal-state-dump"


class SwitchTurnedOn(NeuronIntegrationMessage):
    """Informs Neuron that a managed switch was turned on"""

    type: Literal["switch-turned-on"] = "switch-turned-on"

    unique_id: str


class SwitchTurnedOff(NeuronIntegrationMessage):
    """Informs Neuron that a managed switch was turned off"""

    type: Literal["switch-turned-off"] = "switch-turned-off"

    unique_id: str


class NumberValueChanged(NeuronIntegrationMessage):
    type: Literal["number-value-changed"] = "number-value-changed"
    unique_id: str
    value: float = Field(strict=True, allow_inf_nan=False)


class SelectValueChanged(NeuronIntegrationMessage):
    type: Literal["select-value-changed"] = "select-value-changed"
    unique_id: str
    value: str


class ButtonPressed(NeuronIntegrationMessage):
    """Informs Neuron that a managed button was pressed"""

    type: Literal["button-pressed"] = "button-pressed"

    unique_id: str


#
# Messages from Neuron core
#


class ManagedBinarySensor(BaseModel):
    unique_id: str
    friendly_name: str | None
    automation: str | None  # None means that the entity is managed by Neuron core
    value: bool | None
    device_class: BinarySensorDeviceClass | None
    # attributes: dict[str, str]


class FullUpdate(NeuronCoreMessage):
    """A full update of managed entities

    The integration can assume that any entities that aren't mentioned in
    this message can be disowned or deleted.
    """

    type: Literal["full-update"] = "full-update"
    managed_switches: list[ManagedSwitch]
    managed_binary_sensors: list[ManagedBinarySensor]
    managed_sensors: list[ManagedSensor]
    managed_buttons: list[ManagedButton]
    managed_numbers: list[ManagedNumber] = Field(default_factory=list)
    managed_selects: list[ManagedSelect] = Field(default_factory=list)


class InternalStateDump(NeuronCoreMessage):
    type: Literal["internal-state-dump"] = "internal-state-dump"
    internal_state: dict[str, Any]


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
    value: bool | None
    # attributes: dict[str, str]


class ManagedSensor(BaseModel):
    unique_id: str
    friendly_name: str | None
    value: SensorValue
    # attributes: dict[str, str]
    automation: str | None  # None means that the entity is managed by Neuron core
    device_class: SensorDeviceClass | None
    state_class: SensorStateClass | None
    unit_of_measurement: str | None
    suggested_unit_of_measurement: str | None


class ManagedNumber(BaseModel):
    unique_id: str
    friendly_name: str | None
    automation: str | None
    value: float | None
    min_value: float
    max_value: float
    step: float | None
    unit_of_measurement: str | None
    device_class: NumberDeviceClass | None
    mode: NumberMode


class ManagedSelect(BaseModel):
    unique_id: str
    friendly_name: str | None
    automation: str | None
    value: str | None
    options: list[str]


class ManagedButton(BaseModel):
    unique_id: str
    friendly_name: str | None
    automation: str | None  # None means that the entity is managed by Neuron core


#
# /
#


Message = (
    RequestingFullUpdate
    | RequestingInternalStateDump
    | SwitchTurnedOn
    | SwitchTurnedOff
    | NumberValueChanged
    | SelectValueChanged
    | ButtonPressed
    | FullUpdate
    | InternalStateDump
    | SetValue
)


def parse_message(msg: Mapping[str, Any]) -> Message:
    return RootModel[
        Annotated[
            Message,
            Field(discriminator="type"),
        ]
    ](msg).root  # type: ignore
