"""Neoron's API towards automations"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from functools import wraps
from math import floor
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Coroutine,
    Self,
    Sequence,
    TypeAlias,
    overload,
)

from neuron.logging import NeuronLogger, get_logger

if TYPE_CHECKING:
    from neuron.core import Neuron

__all__ = [
    "on_state_change",
    "on_event",
    "unsubscribe",
    "daily",
    "action",
    "turn_on",
    "turn_off",
    "get_logger",
    "Entity",
    "StateChange",
    "NeuronLogger",
    "SubscriptionHandle",
]

_trigger_handlers: list[tuple[dict, AsyncFunction]] = []
_event_handlers: list[tuple[str, AsyncFunction]] = []
_entities: dict[str, Entity] = {}
_neuron: Neuron | None = None
_automation = ContextVar("automation")
LOG = get_logger(__name__)


@overload
def on_state_change(
    entity: EntityTarget,
    *,
    handler: None = None,
    from_state: str | None = None,
    to_state: str | None = None,
    duration: timedelta | int | str | None = None,
) -> Decorator: ...


@overload
def on_state_change(
    entity: EntityTarget,
    *,
    handler: AsyncFunction,
    from_state: str | None = None,
    to_state: str | None = None,
    duration: timedelta | int | str | None = None,
) -> Coroutine[None, None, SubscriptionHandle]: ...


def on_state_change(
    entity: EntityTarget,
    *,
    handler: AsyncFunction | None = None,
    from_state: str | None = None,
    to_state: str | None = None,
    duration: timedelta | int | str | None = None,
) -> Decorator | Coroutine[None, None, SubscriptionHandle]:
    """Decorator for registering a state change handler

    Can also be used imperatively by providing the handler parameter. The
    decorator form must only be used at the module level. The imperative form
    must only be used from handler functions or init.
    """

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

    return on_trigger(trigger, handler=handler)


@overload
def on_trigger(trigger: dict, *, handler: None = None) -> Decorator: ...


@overload
def on_trigger(
    trigger: dict, *, handler: AsyncFunction
) -> Coroutine[None, None, SubscriptionHandle]: ...


def on_trigger(
    trigger: dict,
    *,
    handler: AsyncFunction | None = None,
) -> Decorator | Coroutine[None, None, SubscriptionHandle]:
    """Decorator for registering an arbitrary trigger subscription

    Can also be used imperatively by providing the handler parameter. The
    decorator form must only be used at the module level. The imperative form
    must only be used from handler functions or init.
    """

    if handler:
        return _subscribe(handler, to=trigger)

    def decorator(handler: AsyncFunction):
        _trigger_handlers.append((trigger, handler))
        return handler

    return decorator


async def _subscribe(
    handler: AsyncFunction,
    *,
    to: str | dict[str, Any],
) -> SubscriptionHandle:
    await _n().subscribe(_automation.get(), handler, to=to)
    return (to, handler)


def on_event(event: str = "*", **filter: Any):
    """Decorator for subscribing to an event, or all events

    Usage examples:

        @on_event()
        async def on_any_event(event_type: str, event: dict, log: NeuronLogger):
            log.info(f"Received event: {event_type=!r} {event=!r}")

        @on_event("zha_event", device_id="0123456789abcdef", command="on")
        async def bedroom_remote_on():
            await bedroom_lights.turn_on()

        @on_event("zha_event", device_id="0123456789abcdef", command="off")
        async def bedroom_remote_off():
            await bedroom_lights.turn_off()
    """

    def decorator(handler: AsyncFunction):
        @wraps(handler)
        async def wrapper(handler_kwargs, *args, **kwargs):
            event = handler_kwargs["event"]

            for key, expected_value in filter.items():
                event_value = event.get(key, None)

                if not event_value:
                    return

                if event_value != expected_value:
                    return

            await handler(*args, **kwargs)

        # Flag this as an event handler wrapper, causing core to pass "handler_kwargs"
        setattr(wrapper, "_event_handler_wrapper", True)

        _event_handlers.append((event, wrapper))

    return decorator


async def unsubscribe(handle: SubscriptionHandle):
    """Unsubscribe from an event using the handle returned from the subscribe function"""

    event_or_trigger, handler = handle

    # Somewhere up in the call stack, the list of automations and handlers are
    # being iterated over, meaning things will go poorly if we mutate that list
    # here and now. Schedule it to be done shortly instead.
    asyncio.create_task(
        _n().unsubscribe(handler, event_or_trigger),
        name=f"unsubscribe-{handler.__name__}",
    )


@overload
def daily(at: str, handler: None = None) -> Decorator: ...


@overload
def daily(
    at: str, handler: AsyncFunction
) -> Coroutine[Any, Any, SubscriptionHandle]: ...


def daily(at: str, handler: AsyncFunction | None = None):
    trigger = {"trigger": "time", "at": at}

    return on_trigger(trigger, handler=handler)


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

    entity_id = _entity_id(entity)
    target = {"entity_id": entity_id} if entity_id else None

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


@overload
def _entity_id(entity: EntityTarget) -> str | list[str]: ...


@overload
def _entity_id(entity: EntityTarget | None) -> str | list[str] | None: ...


def _entity_id(entity: EntityTarget | None) -> str | list[str] | None:
    if entity is None:
        return None
    elif isinstance(entity, Entity):
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
    _clear()

    global _neuron
    _neuron = None


def _clear():
    """Clears registered handlers"""
    global _trigger_handlers, _event_handlers, _entities
    _trigger_handlers = []
    _event_handlers = []
    _entities = {}


class Entity:
    domain: str
    name: str
    entity_id: str
    initialized: asyncio.Event
    _state: str | None
    _attributes: dict[str, str]

    def __init__(self, entity_id: str):
        self.domain, self.name = entity_id.split(".")
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

    def __bool__(self) -> bool:
        """Allows practical usage in conditionals for boolean entities"""

        if self.domain in ["binary_sensor", "switch"]:
            return self.is_on

        raise ValueError("Ambiguous truthiness for entity of domain %r", self.domain)

    @property
    def state(self) -> str:
        # NB: I'm not sure if this can happen, but if it does, I have to make
        # sure all entity states have been set before starting other
        # subscriptions
        if self._state is None:
            raise RuntimeError(f"State for {self.entity_id!r} is not yet initialized")

        return self._state

    @property
    def is_locked(self) -> bool:
        return self.state == "locked"

    @property
    def is_unlocked(self) -> bool:
        return self.state == "unlocked"

    @property
    def is_on(self) -> bool:
        return self.state == "on"

    @property
    def is_off(self) -> bool:
        return self.state == "off"

    @overload
    def on_change(
        self,
        *,
        handler: None = None,
        from_state: str | None = None,
        to_state: str | None = None,
        duration: timedelta | int | str | None = None,
    ) -> Decorator: ...

    @overload
    def on_change(
        self,
        *,
        handler: AsyncFunction,
        from_state: str | None = None,
        to_state: str | None = None,
        duration: timedelta | int | str | None = None,
    ) -> Coroutine[None, None, SubscriptionHandle]: ...

    def on_change(
        self,
        *,
        handler: AsyncFunction | None = None,
        from_state: str | None = None,
        to_state: str | None = None,
        duration: timedelta | int | str | None = None,
    ):
        """Shortcut for on_state_change for this entity"""
        return on_state_change(
            self,
            handler=handler,
            from_state=from_state,
            to_state=to_state,
            duration=duration,
        )

    async def action(
        self,
        action_name: str,
        *,
        domain: str | None = None,
        data: dict[str, Any] | None = None,
    ):
        """Shortcut for performing an action targeting this entity

        Uses the entity's domain by default.
        """

        if domain is None:
            domain = self.domain

        return await action(domain, action_name, entity=self, data=data)

    async def turn_on(self, **data):
        """Shortcut for performing the turn_on action for this entity

        Keyword arguments are forwarded as data to the turn_on action.
        """
        await turn_on(self, **data)

    async def turn_off(self, **data):
        """Shortcut for performing the turn_off action for this entity

        Keyword arguments are forwarded as data to the turn_off action.
        """
        await turn_off(self, **data)

    async def lock(self, **data):
        """Shortcut for locking a lock entity"""
        await self.action("lock", data=data)

    async def unlock(self, **data):
        """Shortcut for unlocking a lock entity"""
        await self.action("lock", data=data)

    async def open_cover(self, **data):
        """Shortcut for opening a cover entity"""
        await self.action("open_cover", data=data)

    async def close_cover(self, **data):
        """Shortcut for closing a cover entity"""
        await self.action("close_cover", data=data)


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
EntityTarget: TypeAlias = str | Entity | Sequence[str | Entity]
SubscriptionHandle: TypeAlias = tuple[str | dict, AsyncFunction]
Decorator: TypeAlias = Callable[[AsyncFunction], AsyncFunction]
