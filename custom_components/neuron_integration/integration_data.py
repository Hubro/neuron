from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from homeassistant.components.button import ButtonEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import bus

# from homeassistant.components.binary_sensor import BinarySensorEntity


@dataclass
class NeuronIntegrationData:
    hass: HomeAssistant
    cleanup_event_listener: Callable[..., Any] | None = None
    platforms_initialized: set[str] = field(default_factory=set)
    add_switch_entities: AddEntitiesCallback | None = None
    add_sensor_entities: AddEntitiesCallback | None = None
    add_button_entities: AddEntitiesCallback | None = None
    switches: list[SwitchEntity] = field(default_factory=list)
    sensors: list[SensorEntity] = field(default_factory=list)
    buttons: list[ButtonEntity] = field(default_factory=list)
    # binary_sensors: list[BinarySensorEntity] = field(default_factory=list)
    entities_created: bool = False

    def add_switches(self, switches: Iterable[bus.ManagedSwitch]):
        assert self.add_switch_entities

        from .switch import ManagedSwitch

        switch_entities = [
            ManagedSwitch(
                self.hass,
                automation=x.automation,
                unique_id=x.unique_id,
                value=x.value,
                friendly_name=x.friendly_name,
            )
            for x in switches
        ]

        self.add_switch_entities(switch_entities)
        self.switches.extend(switch_entities)

    def add_sensors(self, sensors: Iterable[bus.ManagedSensor]):
        assert self.add_sensor_entities

        from .sensor import ManagedSensor

        sensor_entities = [
            ManagedSensor(
                self.hass,
                automation=x.automation,
                unique_id=x.unique_id,
                value=x.value,
                friendly_name=x.friendly_name,
            )
            for x in sensors
        ]

        self.add_sensor_entities(sensor_entities)
        self.sensors.extend(sensor_entities)

    def add_buttons(self, buttons: Iterable[bus.ManagedButton]):
        assert self.add_button_entities

        from .button import ManagedButton

        button_entities = [
            ManagedButton(
                self.hass,
                automation=x.automation,
                unique_id=x.unique_id,
                friendly_name=x.friendly_name,
            )
            for x in buttons
        ]

        self.add_button_entities(button_entities)
        self.buttons.extend(button_entities)
