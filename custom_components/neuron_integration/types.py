from dataclasses import dataclass, field
from typing import Any, Callable

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback


@dataclass
class NeuronIntegrationData:
    cleanup_event_listener: Callable[..., Any] | None = None
    add_switch_entities: AddEntitiesCallback | None = None
    add_sensor_entities: AddEntitiesCallback | None = None
    switches: list[SwitchEntity] = field(default_factory=list)
    entities_created: bool = False

    def add_switches(self, switches: list[SwitchEntity]):
        assert self.add_switch_entities

        self.add_switch_entities(switches)
        self.switches.extend(switches)
