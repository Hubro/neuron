"""Messages types used to communicate between addon and companion integration

This module is symlinked into the integration and dereferenced when installing.
That way they share the exact same code for message parsing.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, Field, RootModel


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
    # managed_sensors: list[ManagedSensor]


class SetState(NeuronCoreMessage):
    """Tell the integration to set the state of a managed entity"""

    type: Literal["set-state"] = "set-state"
    unique_id: str
    state: str

    # TODO: Attributes


class ManagedSwitch(BaseModel):
    unique_id: str
    friendly_name: str
    automation: str | None  # None means that the entity is managed by Neuron core
    state: str
    # attributes: dict[str, str]


#
# /
#


Message = (
    RequestingFullUpdate | SwitchTurnedOn | SwitchTurnedOff | FullUpdate | SetState
)


def parse_message(msg: Mapping[str, Any]) -> Message:
    return RootModel[
        Annotated[
            Message,
            Field(discriminator="type"),
        ]
    ](msg).root  # type: ignore
