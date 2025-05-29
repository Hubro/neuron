"""Neoron's API towards automations"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from math import floor
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Self, TypeAlias

if TYPE_CHECKING:
    from neuron.core import Neuron

__all__ = ["StateChange", "on_state_change", "action"]

_trigger_handlers: list[tuple[dict, StateChangeHandler]] = []
_neuron: Neuron | None = None


def on_state_change(
    entity_id: str | list[str],
    from_state: str | None = None,
    to_state: str | None = None,
    duration: timedelta | int | None = None,
):
    """Decorator for registering a state change handler"""

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
            hours = 0
            minutes = 0
            seconds = duration
        else:
            seconds = floor(duration.total_seconds())

            hours = seconds // (60 * 60)
            seconds %= 60 * 60

            minutes = seconds // 60
            seconds %= 60

        trigger["for"] = f"{hours:02}:{minutes:02}:{seconds:02}"

    def decorator(handler: StateChangeHandler):
        _trigger_handlers.append((trigger, handler))
        return handler

    return decorator


async def action(
    domain: str,
    name: str,
    /,
    entity_id: str | list[str] | None = None,
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

    target = {"entity_id": entity_id} if entity_id else None

    return await _n().perform_action(domain, name, target=target, data=data)


async def turn_on(entity_id: str):
    await action("homeassistant", "turn_on", entity_id)


def _n() -> Neuron:
    if not _neuron:
        raise RuntimeError("Tried to use automation API before Neuron setup")

    return _neuron


def _reset():
    """Returns the API module to a clean initial state"""
    global _neuron, _trigger_handlers
    _neuron = None
    _trigger_handlers = []


@dataclass
class StateChange:
    from_state: str
    to_state: str

    @classmethod
    def from_event_message(cls, msg: Any) -> Self:
        assert msg["type"] == "event"

        event = msg["event"]["variables"]["trigger"]

        return cls(
            from_state=event["from_state"]["state"],
            to_state=event["to_state"]["state"],
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


StateChangeHandler: TypeAlias = Callable[[StateChange], Awaitable[None]]
