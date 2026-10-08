from __future__ import annotations

from math import isfinite
from typing import overload

from neuron import api
from neuron.api import AsyncFunction, Decorator
from neuron.hass_const import NumberDeviceClass, NumberMode
from neuron.managed_entity_platforms.base import ManagedEntity, ValueChange
from neuron.util import filter_keyword_args


class ManagedNumber(ManagedEntity):
    """An editable number. Values and bounds use the configured unit.

    Values outside the bounds raise ValueError, including restored values.
    None represents an unknown value. Step sets the UI increment, not rounding.
    """

    _value: float | None

    def __init__(
        self,
        name: str,
        initial_value: float | None,
        friendly_name: str | None = None,
        *,
        min_value: float = 0,
        max_value: float = 100,
        step: float | None = None,
        unit_of_measurement: str | None = None,
        device_class: NumberDeviceClass | None = None,
        mode: NumberMode = NumberMode.AUTO,
        icon: str | None = None,
    ):
        assert "." not in name, "Name can not contain period symbol"

        for key, value in {"min_value": min_value, "max_value": max_value}.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
            ):
                raise ValueError(f"{key} must be a finite number")
        if min_value >= max_value:
            raise ValueError("min_value must be less than max_value")
        if step is not None and (
            isinstance(step, bool)
            or not isinstance(step, (int, float))
            or not isfinite(step)
            or step <= 0
        ):
            raise ValueError("step must be a positive finite number")

        self.min_value = min_value
        self.max_value = max_value
        self.step = step
        self.unit_of_measurement = unit_of_measurement
        self.device_class = device_class
        self.mode = NumberMode(mode)
        self._handler: AsyncFunction | None = None

        self._validate_value(initial_value)
        if state := api.get_neuron().state.managed_entity_states.get(f"number.{name}"):
            self._validate_value(state.value)

        super().__init__(
            unique_id=f"number.{name}",
            initial_value=initial_value,
            friendly_name=friendly_name,
            icon=icon,
        )

    def _validate_value(self, value: float | None):
        if value is None:
            return
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or not self.min_value <= value <= self.max_value
        ):
            raise ValueError(
                f"Value must be a finite number between {self.min_value} and "
                f"{self.max_value}, or None, got {value!r}"
            )

    @property
    def value(self) -> float | None:
        return self._value

    async def set_value(self, value: float | None, **kwargs):
        self._validate_value(value)
        await self._set_value(value, **kwargs)

    @overload
    def when_changed(self, handler: None = None) -> Decorator: ...

    @overload
    def when_changed(self, handler: AsyncFunction) -> AsyncFunction: ...

    def when_changed(self, handler: AsyncFunction | None = None):
        """Register an async handler accepting change, number, and/or log."""

        def decorator(handler: AsyncFunction, /):
            self._handler = handler
            return handler

        return decorator(handler) if handler is not None else decorator

    async def _on_change(self, change: ValueChange[float | None]):
        if self._handler is not None:
            await self._handler(
                **filter_keyword_args(
                    self._handler,
                    {
                        "change": change,
                        "number": self,
                        "log": api.get_automation_logger(),
                    },
                )
            )
