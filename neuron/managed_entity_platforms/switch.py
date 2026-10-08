from __future__ import annotations

from typing import Literal, overload

from neuron import api
from neuron.api import AsyncFunction, Decorator
from neuron.managed_entity_platforms.base import ManagedEntity, ValueChange
from neuron.util import filter_keyword_args


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
    _value: bool | None

    def __init__(
        self,
        name: str,
        initial_value: Literal["on", "off", True, False] | None,
        friendly_name: str | None = None,
        icon: str | None = None,
    ):
        assert "." not in name, "Name can not contain period symbol"

        self._turned_on_handler = None
        self._turned_off_handler = None

        super().__init__(
            unique_id=f"switch.{name}",
            initial_value=None
            if initial_value is None
            else initial_value in [True, "on"],
            friendly_name=friendly_name,
            icon=icon,
        )

    async def _on_change(self, change: ValueChange[bool | None]):
        assert change.from_value != change.to_value

        if change.to_value is True and self._turned_on_handler is not None:
            kwargs = filter_keyword_args(
                self._turned_on_handler,
                {"log": api.get_automation_logger(), "switch": self},
            )
            await self._turned_on_handler(**kwargs)

        elif change.to_value is False and self._turned_off_handler is not None:
            kwargs = filter_keyword_args(
                self._turned_off_handler,
                {"log": api.get_automation_logger(), "switch": self},
            )
            await self._turned_off_handler(**kwargs)

    @property
    def value(self) -> bool | None:
        return self._value

    @property
    def is_on(self) -> bool:
        return self._value is True

    @property
    def is_off(self) -> bool:
        return self._value is False

    @overload
    def when_turned_on(self, handler: None = None) -> Decorator: ...

    @overload
    def when_turned_on(self, handler: AsyncFunction) -> None: ...

    # Rename to "when_turned_on"?
    def when_turned_on(self, handler: AsyncFunction | None = None):
        def decorator(handler: AsyncFunction, /):
            self._turned_on_handler = handler

            return handler

        if handler:
            decorator(handler)
        else:
            return decorator

    @overload
    def when_turned_off(self, handler: None = None) -> Decorator: ...

    @overload
    def when_turned_off(self, handler: AsyncFunction) -> None: ...

    def when_turned_off(self, handler: AsyncFunction | None = None):
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

    async def set_value(
        self,
        value: Literal[True, "on", False, "off"] | str | None,
        **kwargs,
    ):
        """Set the value of the managed switch

        The type also allows "str" to allow setting the value of another
        switch directly. Values must be "on", "off", or None.
        """

        assert value in [True, "on", False, "off", None]

        await self._set_value(
            None if value is None else value in [True, "on"], **kwargs
        )
