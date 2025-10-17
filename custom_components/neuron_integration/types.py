from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bus import FullUpdate


@dataclass
class NeuronIntegrationData:
    cleanup_event_listener: Callable[..., Any] | None = None
    add_switch_entities: AddEntitiesCallback | None = None
    add_sensor_entities: AddEntitiesCallback | None = None
    full_update: FullUpdate | None = None
