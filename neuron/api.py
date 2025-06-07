"""Neoron's API towards automations"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from math import floor
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Self, TypeAlias

from neuron.logging import get_logger

if TYPE_CHECKING:
    from neuron.core import Neuron

__all__ = [
    "on_state_change",
    "daily",
    "action",
    "turn_on",
    "turn_off",
    "Entity",
    "StateChange",
]

_trigger_handlers: list[tuple[dict, AsyncFunction]] = []
_entities: dict[str, Entity] = {}
_neuron: Neuron | None = None
LOG = get_logger(__name__)


def on_state_change(
    entity: EntityTarget,
    from_state: str | None = None,
    to_state: str | None = None,
    duration: timedelta | int | str | None = None,
):
    """Decorator for registering a state change handler"""

    entity_id = _entity_id(entity)

    trigger = {
        "trigger": "state",
        "entity_id": entity_id,
    }

    if from_state:
        trigger["from"] = from_state
    if to_state:
        trigger["to"] = to_state
    if duration:
        if isinstance(duration, int):
            duration = timedelta(seconds=duration)

        if isinstance(duration, timedelta):
            seconds = floor(duration.total_seconds())

            hours = seconds // (60 * 60)
            seconds %= 60 * 60

            minutes = seconds // 60
            seconds %= 60

            duration = f"{hours:02}:{minutes:02}:{seconds:02}"

        assert isinstance(duration, str)

        trigger["for"] = duration

    def decorator(handler: AsyncFunction):
        _trigger_handlers.append((trigger, handler))
        return handler

    return decorator


def daily(at: str):
    trigger = {"trigger": "time", "at": at}

    def decorator(handler: Callable):
        _trigger_handlers.append((trigger, handler))
        return handler

    return decorator


async def action(
    domain: str,
    name: str,
    /,
    entity: EntityTarget | None = None,
    *,
    area_id: str | None = None,
    device_id: str | None = None,
    label_id: str | None = None,
    data: dict[str, Any] | None = None,
    return_response=False,
) -> Any:
    assert not any([area_id, device_id, label_id]), (
        "Targets other than entity_id not implemented"
    )
    assert not return_response, "return_response not implemented"

    target = {"entity_id": _entity_id(entity)} if entity else None

    if target:
        LOG.info(
            "Performing action %s.%s on %r",
            domain,
            name,
            entity_id,
            extra={"component": "api"},
        )
    else:
        LOG.info("Performing action %s.%s", domain, name, extra={"component": "api"})

    return await _n().hass.perform_action(domain, name, target=target, data=data)


async def turn_on(entity: EntityTarget, **kwargs):
    await action("homeassistant", "turn_on", entity, data=kwargs)


async def turn_off(entity: EntityTarget, **kwargs):
    await action("homeassistant", "turn_off", entity, data=kwargs)


def _entity_id(entity: EntityTarget) -> str | list[str]:
    if isinstance(entity, Entity):
        entity = str(entity)
    elif isinstance(entity, list):
        entity = [str(x) for x in entity]

    return entity  # type: ignore


def _n() -> Neuron:
    if not _neuron:
        raise RuntimeError("Tried to use automation API before Neuron setup")

    return _neuron


def _reset():
    """Returns the API module to a clean initial state"""
    global _neuron, _trigger_handlers
    _neuron = None
    _trigger_handlers = []


class Entity:
    entity_id: str
    initialized: asyncio.Event
    _state: str | None
    _attributes: dict[str, str]

    def __init__(self, entity_id: str):
        self.entity_id = entity_id
        self._state = None
        self.initialized = asyncio.Event()

        _entities[entity_id] = self

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.entity_id} state={self._state!r}>"

    def __str__(self) -> str:
        return self.entity_id

    def __getattr__(self, name: str, /) -> Any:
        """Allows nicer attribute lookup"""

        if attr := self._attributes.get(name, None):
            return attr

        raise AttributeError(name=name)

    def __hash__(self) -> int:
        return hash(self.entity_id)

    @property
    def state(self) -> str:
        # NB: I'm not sure if this can happen, but if it does, I have to make
        # sure all entity states have been set before starting other
        # subscriptions
        if self._state is None:
            raise RuntimeError(f"State for {self.entity_id!r} is not yet initialized")

        return self._state


@dataclass
class StateChange:
    from_state: str
    to_state: str

    @classmethod
    def from_event_message(cls, msg: Any) -> Self:
        assert msg["type"] == "event"

        trigger = msg["event"]["variables"]["trigger"]
        assert trigger["platform"] == "state"

        return cls(
            from_state=trigger["from_state"]["state"],
            to_state=trigger["to_state"]["state"],
        )

        # Example state change event:
        #
        # {
        #     "id": 2,
        #     "type": "event",
        #     "event": {
        #         "variables": {
        #             "trigger": {
        #                 "id": "0",
        #                 "idx": "0",
        #                 "alias": None,
        #                 "platform": "state",
        #                 "entity_id": "light.bathroom_ceiling",
        #                 "from_state": {
        #                     "entity_id": "light.bathroom_ceiling",
        #                     "state": "off",
        #                     "attributes": {
        #                         "min_color_temp_kelvin": 2000,
        #                         "max_color_temp_kelvin": 6535,
        #                         "min_mireds": 153,
        #                         "max_mireds": 500,
        #                         "effect_list": ["off", "colorloop"],
        #                         "supported_color_modes": ["color_temp", "xy"],
        #                         "effect": None,
        #                         "color_mode": None,
        #                         "brightness": None,
        #                         "color_temp_kelvin": None,
        #                         "color_temp": None,
        #                         "hs_color": None,
        #                         "rgb_color": None,
        #                         "xy_color": None,
        #                         "off_with_transition": False,
        #                         "off_brightness": 254,
        #                         "icon": "mdi:ceiling-light",
        #                         "friendly_name": "Light - Bathroom - Ceiling light",
        #                         "supported_features": 44,
        #                     },
        #                     "last_changed": "2025-05-29T18:02:18.640400+00:00",
        #                     "last_reported": "2025-05-29T18:02:18.640400+00:00",
        #                     "last_updated": "2025-05-29T18:02:18.640400+00:00",
        #                     "context": {
        #                         "id": "01JWEKB3HCHWJY34EHAARM9ZBN",
        #                         "parent_id": None,
        #                         "user_id": "84b245625412434a97f0f56178b1fab7",
        #                     },
        #                 },
        #                 "to_state": {
        #                     "entity_id": "light.bathroom_ceiling",
        #                     "state": "on",
        #                     "attributes": {
        #                         "min_color_temp_kelvin": 2000,
        #                         "max_color_temp_kelvin": 6535,
        #                         "min_mireds": 153,
        #                         "max_mireds": 500,
        #                         "effect_list": ["off", "colorloop"],
        #                         "supported_color_modes": ["color_temp", "xy"],
        #                         "effect": "off",
        #                         "color_mode": "color_temp",
        #                         "brightness": 254,
        #                         "color_temp_kelvin": 5025,
        #                         "color_temp": 199,
        #                         "hs_color": [27.028, 18.905],
        #                         "rgb_color": [255, 229, 207],
        #                         "xy_color": [0.37, 0.35],
        #                         "off_with_transition": False,
        #                         "off_brightness": None,
        #                         "icon": "mdi:ceiling-light",
        #                         "friendly_name": "Light - Bathroom - Ceiling light",
        #                         "supported_features": 44,
        #                     },
        #                     "last_changed": "2025-05-29T18:02:23.244559+00:00",
        #                     "last_reported": "2025-05-29T18:02:23.244559+00:00",
        #                     "last_updated": "2025-05-29T18:02:23.244559+00:00",
        #                     "context": {
        #                         "id": "01JWEKB80RNQ1BD31CJ9MNBXNX",
        #                         "parent_id": None,
        #                         "user_id": "84b245625412434a97f0f56178b1fab7",
        #                     },
        #                 },
        #                 "for": {
        #                     "__type": "<class 'datetime.timedelta'>",
        #                     "total_seconds": 3.0,
        #                 },
        #                 "attribute": None,
        #                 "description": "state of light.bathroom_ceiling",
        #             }
        #         },
        #         "context": {
        #             "id": "01JWEKB80RNQ1BD31CJ9MNBXNX",
        #             "parent_id": None,
        #             "user_id": "84b245625412434a97f0f56178b1fab7",
        #         },
        #     },
        # }


AsyncFunction: TypeAlias = Callable[..., Awaitable[Any]]
EntityTarget: TypeAlias = str | Entity | list[str | Entity]
