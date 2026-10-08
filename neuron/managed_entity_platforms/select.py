from __future__ import annotations

from typing import Sequence, overload

import neuron.api as api
from neuron.api import AsyncFunction, Decorator
from neuron.managed_entity_platforms.base import ManagedEntity, ValueChange
from neuron.util import filter_keyword_args


class ManagedSelect(ManagedEntity):
    """An editable choice from a fixed list of options.

    None represents an unknown value. Values outside the options raise
    ValueError, including restored values.
    """

    _value: str | None

    def __init__(
        self,
        name: str,
        initial_value: str | None,
        friendly_name: str | None = None,
        *,
        options: Sequence[str],
        icon: str | None = None,
    ):
        assert "." not in name, "Name can not contain period symbol"

        if isinstance(options, str) or not options:
            raise ValueError("options must be a non-empty sequence of strings")
        if any(not isinstance(option, str) or not option for option in options):
            raise ValueError("Each option must be a non-empty string")
        if len(set(options)) != len(options):
            raise ValueError("options must be unique")

        self.options = tuple(options)
        self._handler: AsyncFunction | None = None
        self._validate_value(initial_value)
        if state := api.get_neuron().state.managed_entity_states.get(f"select.{name}"):
            self._validate_value(state.value)

        super().__init__(
            unique_id=f"select.{name}",
            initial_value=initial_value,
            friendly_name=friendly_name,
            icon=icon,
        )

    def _validate_value(self, value: str | None):
        if value is not None and value not in self.options:
            raise ValueError(
                f"Value must be one of {self.options!r}, or None, got {value!r}"
            )

    @property
    def value(self) -> str | None:
        return self._value

    async def set_value(self, value: str | None, **kwargs):
        self._validate_value(value)
        await self._set_value(value, **kwargs)

    @overload
    def when_changed(self, handler: None = None) -> Decorator: ...

    @overload
    def when_changed(self, handler: AsyncFunction) -> AsyncFunction: ...

    def when_changed(self, handler: AsyncFunction | None = None):
        """Register an async handler accepting change, select, and/or log."""

        def decorator(handler: AsyncFunction, /):
            self._handler = handler
            return handler

        return decorator(handler) if handler is not None else decorator

    async def _on_change(self, change: ValueChange[str | None]):
        if self._handler is not None:
            await self._handler(
                **filter_keyword_args(
                    self._handler,
                    {
                        "change": change,
                        "select": self,
                        "log": api.get_automation_logger(),
                    },
                )
            )
