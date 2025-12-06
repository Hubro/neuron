from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from homeassistant.components.button import ButtonEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

# from homeassistant.components.binary_sensor import BinarySensorEntity
# from homeassistant.components.button import ButtonEntity


@dataclass
class NeuronIntegrationData:
    cleanup_event_listener: Callable[..., Any] | None = None
    add_switch_entities: AddEntitiesCallback | None = None
    add_sensor_entities: AddEntitiesCallback | None = None
    add_button_entities: AddEntitiesCallback | None = None
    switches: list[SwitchEntity] = field(default_factory=list)
    sensors: list[SensorEntity] = field(default_factory=list)
    buttons: list[ButtonEntity] = field(default_factory=list)
    # binary_sensors: list[BinarySensorEntity] = field(default_factory=list)
    # buttons: list[ButtonEntity] = field(default_factory=list)
    entities_created: bool = False

    def add_switches(self, switches: Iterable[SwitchEntity]):
        assert self.add_switch_entities

        switches = list(switches)

        self.add_switch_entities(switches)
        self.switches.extend(switches)

    def add_sensors(self, sensors: Iterable[SensorEntity]):
        assert self.add_sensor_entities

        sensors = list(sensors)

        self.add_sensor_entities(sensors)
        self.sensors.extend(sensors)

    def add_buttons(self, buttons: Iterable[ButtonEntity]):
        assert self.add_button_entities

        buttons = list(buttons)

        self.add_button_entities(buttons)
        self.buttons.extend(buttons)
