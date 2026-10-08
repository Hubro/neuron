from __future__ import annotations

from neuron import api
from neuron.api import AsyncFunction
from neuron.managed_entity_platforms.base import ManagedEntity


class ManagedButton(ManagedEntity):
    """A button entity created and managed by Neuron

    Usage:

        my_button = ManagedButton("launch_missiles", friendly_name="Send friendly reminder")

        @my_button.when_pressed
        async def launch(log):
            log.info("The eagle has left the nest")
    """

    def __init__(
        self,
        name: str,
        friendly_name: str | None = None,
        icon: str | None = None,
    ):
        assert "." not in name, "Name can not contain period symbol"

        self._handler: AsyncFunction | None = None

        super().__init__(
            unique_id=f"button.{name}",
            friendly_name=friendly_name,
            icon=icon,
        )

    @property
    def value(self):
        raise NotImplementedError("Buttons have no value")

    async def _on_change(self, *args, **kwargs):
        raise NotImplementedError("Buttons have no value to change")

    def when_pressed(self, fn: AsyncFunction | None):
        def decorator(fn: AsyncFunction):
            self._handler = fn
            return fn

        if fn:
            return decorator(fn)
        else:
            return decorator

    async def press(self):
        """Executes the event handler set with 'when_pressed'"""

        api.get_automation_logger().info("Managed button pressed: %r", self)

        if self._handler:
            await self._handler()
