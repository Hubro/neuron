"""For storing persistent state to disk"""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Self

import orjson

from .config import load_config
from .logging import get_logger

LOG = get_logger(__name__)


@dataclass
class NeuronState:
    enabled: bool = True
    automations: dict[str, AutomationState] = field(default_factory=dict)
    managed_entity_states: dict[str, ManagedEntityState] = field(default_factory=dict)

    @staticmethod
    def get_path() -> Path:
        config = load_config()
        return config.data_dir / "persistent-state.json"

    @classmethod
    def load(cls) -> Self:
        LOG.info("Loading persistent state")

        state_path = cls.get_path()

        if state_path.exists():
            assert state_path.is_file()

            try:
                raw_state = orjson.loads(state_path.read_bytes())
                state = cls.from_dict(raw_state)
            except Exception:
                LOG.error(
                    "Failed to read persistent config, please fix or delete the file"
                )
                raise

            LOG.debug("Loaded persistent state: %r", state)
        else:
            LOG.info("Persistent state file not found, creating a new one")
            state = cls()
            state.save()

        return state

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        automations = d.setdefault("automations", {})

        for name, automation in list(automations.items()):
            if isinstance(automation, dict):
                automations[name] = AutomationState(**automation)

        managed_entity_states = d.setdefault("managed_entity_states", {})

        for entity_id, state in list(managed_entity_states.items()):
            if isinstance(state, dict):
                managed_entity_states[entity_id] = ManagedEntityState(**state)

        return cls(**d)

    # TODO: Instead of saving directly to disk, instead set a flag that causes
    # a separate thread to do the saving, avoiding the main thread locking up
    # because of unresponsive storage
    def save(self):
        LOG.info("Saving persistent state to disc")

        config = load_config()
        state_path = self.get_path()

        with tempfile.NamedTemporaryFile(
            prefix=".persistent-state.json.",
            dir=config.data_dir,
        ) as tmp:
            LOG.trace("Writing state to temporary file %r", tmp.name)
            tmp.write(orjson.dumps(asdict(self)))

            LOG.trace("Renaming temporary file to %r", str(state_path))
            os.replace(tmp.name, state_path)

    def _dump_state(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AutomationState:
    enabled: bool = True


@dataclass
class ManagedEntityState:
    value: Any
    attributes: dict[str, str] = field(default_factory=dict)


if __name__ == "__main__":
    state = NeuronState(
        automations={
            "foo": AutomationState(enabled=True),
            "bar": AutomationState(enabled=False),
        },
        managed_entity_states={
            "switch.foo": ManagedEntityState("on"),
            "switch.bar": ManagedEntityState("off"),
        },
    )
    dict_state = asdict(state)

    print(f"{state=}")
    print(f"{dict_state=}")

    print(f"{NeuronState.from_dict(dict_state)=}")
