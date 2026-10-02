from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TypeAlias

from neuron.hass_const import SensorDeviceClass, SensorStateClass
from neuron.managed_entity_platforms.base import ManagedEntity, ValueChange

SensorValue: TypeAlias = str | int | float | date | datetime | Decimal | None


class ManagedSensor[T = SensorValue](ManagedEntity):
    """A sensor entity created and managed by Neuron

    Usage:

        my_sensor = ManagedSensor("price_of_eggs", initial_value=0.12)

        def init():
            await my_sensor.set_value(999.99)

            # Or
            await my_sensor.set_state("999.99")
    """

    _value: T | None

    def __init__(
        self,
        name: str,
        initial_value: T | None,
        friendly_name: str | None = None,
        device_class: SensorDeviceClass | None = None,
        state_class: SensorStateClass | None = None,
        unit_of_measurement: str | None = None,
        suggested_unit_of_measurement: str | None = None,
    ):
        assert "." not in name, "Name can not contain period symbol"

        self.device_class = device_class
        self.state_class = state_class
        self.unit_of_measurement = unit_of_measurement
        self.suggested_unit_of_measurement = suggested_unit_of_measurement

        super().__init__(
            unique_id=f"sensor.{name}",
            initial_value=initial_value,
            friendly_name=friendly_name,
        )

    @property
    def value(self) -> T | None:
        return self._value

    async def set_value(self, value: T | None, **kwargs):
        await self._set_value(value, **kwargs)

    async def _on_change(self, change: ValueChange[T | None]):
        assert change.from_value != change.to_value
