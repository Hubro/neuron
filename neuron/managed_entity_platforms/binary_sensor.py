from __future__ import annotations

from typing import Literal

from neuron.hass_const import BinarySensorDeviceClass
from neuron.managed_entity_platforms.base import ManagedEntity, ValueChange


class ManagedBinarySensor(ManagedEntity):
    """A binary sensor entity created and managed by Neuron"""

    _value: bool | None

    def __init__(
        self,
        name: str,
        initial_value: Literal["on", "off", True, False] | None,
        friendly_name: str | None = None,
        device_class: BinarySensorDeviceClass | None = None,
    ):
        assert "." not in name, "Name can not contain period symbol"

        self.device_class = device_class

        super().__init__(
            unique_id=f"binary_sensor.{name}",
            initial_value=None
            if initial_value is None
            else initial_value in [True, "on"],
            friendly_name=friendly_name,
        )

    @property
    def value(self) -> bool | None:
        return self._value

    @property
    def is_on(self) -> bool:
        return self._value is True

    @property
    def is_off(self) -> bool:
        return self._value is False

    async def set_value(
        self,
        value: Literal[True, "on", False, "off"] | str | None,
        **kwargs,
    ):
        assert value in [True, "on", False, "off", None]

        await self._set_value(
            None if value is None else value in [True, "on"], **kwargs
        )

    async def _on_change(self, change: ValueChange[bool | None]):
        assert change.from_value != change.to_value
