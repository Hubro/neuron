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
class NeuronIntegrationContext:
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

        existing_switches = set(x.unique_id for x in self.switches)

        new_switch_entities = [
            ManagedSwitch(
                self.hass,
                automation=x.automation,
                unique_id=x.unique_id,
                value=x.value,
                friendly_name=x.friendly_name,
            )
            for x in switches
            if x.unique_id not in existing_switches
        ]

        if not new_switch_entities:
            return

        self.add_switch_entities(new_switch_entities)
        self.switches.extend(new_switch_entities)

    def add_sensors(self, sensors: Iterable[bus.ManagedSensor]):
        assert self.add_sensor_entities

        from .sensor import ManagedSensor

        existing_sensors = set(x.unique_id for x in self.sensors)

        new_sensor_entities = [
            ManagedSensor(
                self.hass,
                automation=x.automation,
                unique_id=x.unique_id,
                value=x.value,
                friendly_name=x.friendly_name,
            )
            for x in sensors
            if x.unique_id not in existing_sensors
        ]

        if not new_sensor_entities:
            return

        self.add_sensor_entities(new_sensor_entities)
        self.sensors.extend(new_sensor_entities)

    def add_buttons(self, buttons: Iterable[bus.ManagedButton]):
        assert self.add_button_entities

        from .button import ManagedButton

        existing_buttons = set(x.unique_id for x in self.buttons)

        new_button_entities = [
            ManagedButton(
                self.hass,
                automation=x.automation,
                unique_id=x.unique_id,
                friendly_name=x.friendly_name,
            )
            for x in buttons
            if x.unique_id not in existing_buttons
        ]

        if not new_button_entities:
            return

        self.add_button_entities(new_button_entities)
        self.buttons.extend(new_button_entities)
