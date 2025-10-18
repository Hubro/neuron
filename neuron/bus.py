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
    """Requests a full dump of Sedrec's state"""

    type: Literal["requesting-full-update"] = "requesting-full-update"


class UpdateAutomation(NeuronIntegrationMessage):
    """Updates the state of an automation"""

    type: Literal["update-automation"] = "update-automation"

    automation: str  # Automation module name
    enabled: bool | None = None


#
# Messages from Neuron core
#


class FullUpdate(NeuronCoreMessage):
    type: Literal["full-update"] = "full-update"
    automations: list[Automation]


class Automation(BaseModel):
    name: str
    module_name: str
    enabled: bool
    trigger_subscriptions: int
    event_subscriptions: int
    state_subscriptions: int


#
# /
#


Message = RequestingFullUpdate | UpdateAutomation | FullUpdate


def parse_message(msg: Mapping[str, Any]) -> Message:
    return RootModel[
        Annotated[
            Message,
            Field(discriminator="type"),
        ]
    ](msg).root  # type: ignore
