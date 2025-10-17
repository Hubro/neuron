"""Messages types used to communicate between addon and companion integration

This module is symlinked into the integration and dereferenced when installing.
That way they share the exact same code for message parsing.
"""

from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, Field, RootModel


class BaseMessage(BaseModel):
    pass


class RequestingFullUpdate(BaseMessage):
    """Used by the integration to request a full dump of Sedrec's state"""

    type: Literal["requesting-full-update"] = "requesting-full-update"


class FullUpdate(BaseMessage):
    type: Literal["full-update"] = "full-update"
    automations: "list[Automation]"


Message = RequestingFullUpdate | FullUpdate


class Automation(BaseModel):
    name: str
    enabled: bool
    trigger_subscriptions: int
    event_subscriptions: int
    state_subscriptions: int


def parse_message(msg: Mapping[str, Any]) -> "Message":
    return RootModel[
        Annotated[
            Message,
            Field(discriminator="type"),
        ]
    ](msg).root  # type: ignore
