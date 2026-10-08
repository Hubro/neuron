from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from neuron import api
from neuron.managed_entities import ManagedEntities
from neuron.state import NeuronState


@pytest.fixture
def managed_neuron(monkeypatch):
    neuron = SimpleNamespace(
        state=NeuronState(),
        hass=SimpleNamespace(fire_event=AsyncMock()),
        automations={},
    )
    # Only the Neuron attributes used by managed entities are mocked.
    neuron.managed_entities = ManagedEntities(neuron)  # ty: ignore[invalid-argument-type]
    monkeypatch.setattr(neuron.state, "save", Mock())
    monkeypatch.setattr(api, "_neuron", neuron)
    monkeypatch.setattr(api, "_managed_entities", {})
    token = api._automation.set(None)
    try:
        yield neuron
    finally:
        api._automation.reset(token)
