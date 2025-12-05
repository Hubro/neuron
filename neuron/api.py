"""Neoron's API towards automations"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import wraps
from math import floor
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Coroutine,
    Iterable,
    Literal,
    Self,
    Sequence,
    TypeAlias,
    TypeVar,
    cast,
    overload,
)

from neuron.debounce import debounce
from neuron.hass_const import SensorDeviceClass, SensorStateClass
from neuron.logging import NeuronLogger, get_logger
from neuron.util import filter_keyword_args

if TYPE_CHECKING:
    from neuron.core import Neuron

__all__ = [
    "on_state_change",
    "on_trigger",
    "on_event",
    "unsubscribe",
    "daily",
    "action",
    "turn_on",
    "turn_off",
    "get_logger",
    "debounce",
    "Entity",
    "ManagedSwitch",
    "ManagedSensor",
    "SensorValue",
    "StateChange",
    "ValueChange",
    "NeuronLogger",
    "SubscriptionHandle",
    # Re-export of Home Assistant constants:
    "SensorDeviceClass",
    "SensorStateClass",
]

_trigger_handlers: list[tuple[dict, AsyncFunction]] = []
_event_handlers: list[tuple[str, AsyncFunction]] = []
_entities: dict[str, Entity] = {}
_managed_entities: dict[str, ManagedEntity] = {}

_neuron: Neuron | None = None
_automation = ContextVar("automation")
_logger = ContextVar("logger", default=get_logger(__name__))


@overload
def on_state_change(
    entity: EntityTarget,
    *,
    handler: None = None,
    from_state: str | Iterable[str] | None = None,
    to_state: str | Iterable[str] | None = None,
    not_from_state: str | Iterable[str] | None = None,
    not_to_state: str | Iterable[str] | None = None,
    duration: timedelta | int | str | None = None,
) -> Decorator: ...


@overload
def on_state_change(
    entity: EntityTarget,
    *,
    handler: AsyncFunction,
    from_state: str | Iterable[str] | None = None,
    to_state: str | Iterable[str] | None = None,
    not_from_state: str | Iterable[str] | None = None,
    not_to_state: str | Iterable[str] | None = None,
    duration: timedelta | int | str | None = None,
) -> Coroutine[None, None, SubscriptionHandle]: ...


def on_state_change(
    entity: EntityTarget,
    *,
    handler: AsyncFunction | None = None,
    from_state: str | Iterable[str] | None = None,
    to_state: str | Iterable[str] | None = None,
    not_from_state: str | Iterable[str] | None = None,
    not_to_state: str | Iterable[str] | None = None,
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

    def flatten(state: str | Iterable[str] | None) -> str | list[str] | None:
        if isinstance(state, str) or state is None:
            return state
        else:
            return list(state)

    if from_state:
        trigger["from"] = flatten(from_state)
    if to_state:
        trigger["to"] = flatten(to_state)
    if not_from_state:
        trigger["not_from"] = flatten(not_from_state)
    if not_to_state:
        trigger["not_to"] = flatten(not_to_state)
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
    _l().info("Subscribing handler %r to %r", handler.__name__, to)
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

    # TODO: Allow dynamic invocation, as with on_trigger

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

            _l().info("Executing event handler: %s", handler.__name__)
            await handler(*args, **kwargs)

        # Flag this as an event handler wrapper, causing core to pass "handler_kwargs"
        setattr(wrapper, "_event_handler_wrapper", True)

        _event_handlers.append((event, wrapper))

    return decorator


async def unsubscribe(handle: SubscriptionHandle):
    """Unsubscribe from an event using the handle returned from the subscribe function"""

    event_or_trigger, handler = handle

    _l().info("Unsubscribing handler %r from %r", handler.__name__, event_or_trigger)

    await _n().unsubscribe(handler, event_or_trigger)


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
        _l().info(
            "Performing action %s.%s on %r",
            domain,
            name,
            entity_id,
            extra={"component": "api"},
        )
    else:
        _l().info("Performing action %s.%s", domain, name, extra={"component": "api"})

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


def _l() -> NeuronLogger:
    return _logger.get()


def _reset():
    """Returns the API module to a clean initial state"""
    _clear()

    global _neuron
    _neuron = None


def _clear():
    """Clears automation buffers"""
    global _trigger_handlers, _event_handlers, _entities, _managed_entities

    _trigger_handlers = []
    _event_handlers = []
    _entities = {}
    _managed_entities = {}


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

        if self.domain in ["binary_sensor", "switch", "input_boolean"]:
            return self.is_on

        raise ValueError("Ambiguous truthiness for entity of domain %r", self.domain)

    @property
    def state(self) -> str:
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
        from_state: str | Iterable[str] | None = None,
        to_state: str | Iterable[str] | None = None,
        not_from_state: str | Iterable[str] | None = None,
        not_to_state: str | Iterable[str] | None = None,
        duration: timedelta | int | str | None = None,
    ) -> Decorator: ...

    @overload
    def on_change(
        self,
        h: AsyncFunction,
    ) -> AsyncFunction: ...

    @overload
    def on_change(
        self,
        *,
        handler: AsyncFunction,
        from_state: str | Iterable[str] | None = None,
        to_state: str | Iterable[str] | None = None,
        not_from_state: str | Iterable[str] | None = None,
        not_to_state: str | Iterable[str] | None = None,
        duration: timedelta | int | str | None = None,
    ) -> Coroutine[None, None, SubscriptionHandle]: ...

    def on_change(
        self,
        h: AsyncFunction | None = None,
        *,
        handler: AsyncFunction | None = None,
        from_state: str | Iterable[str] | None = None,
        to_state: str | Iterable[str] | None = None,
        not_from_state: str | Iterable[str] | None = None,
        not_to_state: str | Iterable[str] | None = None,
        duration: timedelta | int | str | None = None,
    ):
        """Shortcut for on_state_change for this entity

        If a positional argument was given, we assume this function was used
        directly as a decorator, for example:

            @MyEntity.on_change
            async def foo():
                ...
        """

        result = on_state_change(
            self,
            handler=handler,
            from_state=from_state,
            to_state=to_state,
            not_from_state=not_from_state,
            not_to_state=not_to_state,
            duration=duration,
        )

        if h:
            result = cast(Decorator, result)
            return result(h)

        return result

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

    async def stop_cover(self, **data):
        """Shortcut for stopping a cover entity"""
        await self.action("stop_cover", data=data)


class ManagedEntity(ABC):
    """Base class for entities created and managed by Neuron"""

    unique_id: str
    domain: str
    name: str
    friendly_name: str | None

    _value: Any  # Depends on the entity type

    def __init__(
        self,
        unique_id: str,
        initial_value: Any,
        friendly_name: str | None = None,
    ):
        assert unique_id.count(".") == 1, "unique_id must contain 1 period symbol"

        self.unique_id = unique_id
        self.domain, self.name = unique_id.split(".")
        self.friendly_name = friendly_name

        if x := _n().state.managed_entity_states.get(unique_id):
            self._value = x.value
        else:
            self._value = initial_value

        # Only register managed entities while loading an automation, that way
        # Neuron Core can use this API as well.
        if _automation.get(None):
            _managed_entities[unique_id] = self

    @abstractmethod
    async def _on_change(self, change: ValueChange):
        raise NotImplementedError("Subclasses should override this method")

    @property
    def value(self) -> Any:
        return self._value

    async def _set_value(self, value: Any, *, suppress_handler: bool = False):
        """Sets the value of this managed entity and transmits it to the integration

        If suppress_handler is True, the _on_change handler won't be called.
        This is used when the automation is disabled, allowing the managed
        entities to keep working and have the correct value when the
        automation is resumed.
        """

        old_value = self._value

        if value == old_value:
            return

        self._value = value

        # TODO: Attributes

        await _n().set_managed_entity_value(self.unique_id, value)

        if not suppress_handler:
            await self._on_change(
                **filter_keyword_args(
                    self._on_change,
                    {
                        "change": ValueChange(
                            from_value=old_value,
                            to_value=value,
                        ),
                        "log": _l(),
                    },
                )
            )


class ManagedSwitch(ManagedEntity):
    """A switch entity created and managed by Neuron

    Usage:

        my_switch = ManagedSwitch("shields_up")

        @my_switch.turned_on
        async def on(log: NeuronLogger):
            log.info("Shields are up!")

        @my_switch.turned_off
        async def off(log: NeuronLogger):
            log.info("Shields down")
    """

    _turned_on_handler: AsyncFunction | None
    _turned_off_handler: AsyncFunction | None

    def __init__(
        self,
        name: str,
        initial_value: Literal["on", "off", True, False],
        friendly_name: str | None = None,
    ):
        assert "." not in name, "Name can not contain period symbol"

        super().__init__(
            unique_id=f"switch.{name}",
            initial_value=True if initial_value in [True, "on"] else False,
            friendly_name=friendly_name,
        )

    async def _on_change(self, change: ValueChange[bool]):
        assert change.from_value != change.to_value

        if change.to_value is True and self._turned_on_handler is not None:
            kwargs = filter_keyword_args(self._turned_on_handler, {"log": _l()})
            await self._turned_on_handler(**kwargs)

        elif change.to_value is False and self._turned_off_handler is not None:
            kwargs = filter_keyword_args(self._turned_off_handler, {"log": _l()})
            await self._turned_off_handler(**kwargs)

    @property
    def value(self) -> bool:
        return self._value

    @property
    def is_on(self) -> bool:
        return self._value is True

    @property
    def is_off(self) -> bool:
        return self._value is False

    @overload
    def turned_on(self, handler: None = None) -> Decorator: ...

    @overload
    def turned_on(self, handler: AsyncFunction) -> None: ...

    def turned_on(self, handler: AsyncFunction | None = None):
        def decorator(handler: AsyncFunction, /):
            self._turned_on_handler = handler

            return handler

        if handler:
            decorator(handler)
        else:
            return decorator

    @overload
    def turned_off(self, handler: None = None) -> Decorator: ...

    @overload
    def turned_off(self, handler: AsyncFunction) -> None: ...

    def turned_off(self, handler: AsyncFunction | None = None):
        def decorator(handler: AsyncFunction, /):
            self._turned_off_handler = handler

            return handler

        if handler:
            decorator(handler)
        else:
            return decorator

    async def turn_on(self):
        await self.set_value(True)

    async def turn_off(self):
        await self.set_value(False)

    async def toggle(self) -> bool:
        """Toggles the switch and returns the new toggled state"""

        if self.is_on:
            await self.turn_off()
            return False
        else:
            await self.turn_on()
            return True

    async def set_value(self, value: Literal[True, "on", False, "off"] | str, **kwargs):
        """Set the value of the managed switch

        The type also allows "str" to allow setting the value of another
        switch directly. Just make sure the value is "on" or "off" first!
        """

        assert value in [True, "on", False, "off"]

        await self._set_value(True if value in [True, "on"] else False, **kwargs)


SensorValue: TypeAlias = str | int | float | date | datetime | Decimal


class ManagedSensor[T = SensorValue](ManagedEntity):
    """A sensor entity created and managed by Neuron

    Usage:

        my_sensor = ManagedSensor("price_of_eggs", initial_value=0.12)

        def init():
            await my_sensor.set_value(999.99)

            # Or
            await my_sensor.set_state("999.99")
    """

    _value: T

    def __init__(
        self,
        name: str,
        initial_value: T,
        friendly_name: str | None = None,
        device_class: SensorDeviceClass | None = None,
        state_class: SensorStateClass | None = None,
        native_unit_of_measurement: str | None = None,
        suggested_unit_of_measurement: str | None = None,
    ):
        assert "." not in name, "Name can not contain period symbol"

        self.device_class = device_class
        self.state_class = state_class
        self.native_unit_of_measurement = native_unit_of_measurement
        self.suggested_unit_of_measurement = suggested_unit_of_measurement

        super().__init__(
            unique_id=f"sensor.{name}",
            initial_value=initial_value,
            friendly_name=friendly_name,
        )

    @property
    def value(self) -> T:
        return self._value

    async def set_value(self, value: T, **kwargs):
        await self._set_value(value, **kwargs)

    async def _on_change(self, change: ValueChange[T]):
        assert change.from_value != change.to_value


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


@dataclass
class ValueChange[T = Any]:
    from_value: T
    to_value: T


AsyncFunction: TypeAlias = Callable[..., Awaitable[Any]]
EntityTarget: TypeAlias = str | Entity | Sequence[str | Entity]
SubscriptionHandle: TypeAlias = tuple[str | dict, AsyncFunction]
HandlerFn = TypeVar("HandlerFn", default=AsyncFunction)
Decorator: TypeAlias = Callable[[HandlerFn], HandlerFn]
