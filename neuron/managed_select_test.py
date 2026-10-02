from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import Event, HomeAssistant
from pydantic import ValidationError

from custom_components.neuron_integration import bus as integration_bus
from custom_components.neuron_integration.context import NeuronIntegrationContext
from custom_components.neuron_integration.select import ManagedSelect as HassSelect
from neuron import ManagedSelect, ValueChange, api, bus
from neuron.state import ManagedEntityState, NeuronState


def select_payload(**kwargs):
    return integration_bus.ManagedSelect(
        **{
            "unique_id": "select.mode",
            "friendly_name": "Mode",
            "automation": "example",
            "value": "auto",
            "options": ["auto", "eco", "comfort"],
            **kwargs,
        }
    )


def full_update(**kwargs):
    return bus.FullUpdate(
        managed_switches=[],
        managed_binary_sensors=[],
        managed_sensors=[],
        managed_buttons=[],
        **kwargs,
    )


async def test_select_changes_persist_and_call_handler_once(managed_neuron):
    options = ["auto", "eco"]
    select = ManagedSelect("mode", initial_value="auto", options=options)
    options.append("invalid")
    assert select.options == ("auto", "eco")
    changes = []

    @select.when_changed
    async def changed(select, change, log):
        changes.append((select, change, log))

    await select.set_value("eco")
    await select.set_value("eco")

    assert select.value == "eco"
    assert changes == [(select, ValueChange("auto", "eco"), api._l())]
    assert callable(changed)
    managed_neuron.state.save.assert_called_once()
    event = managed_neuron.hass.fire_event.call_args.kwargs["data"]
    assert bus.parse_message(event) == bus.SetValue(
        unique_id="select.mode", value="eco"
    )
    managed_neuron.state = NeuronState.from_dict(managed_neuron.state._dump_state())
    assert (
        ManagedSelect("mode", initial_value="auto", options=select.options).value
        == "eco"
    )


async def test_select_unknown_values_and_filtered_callback_arguments(managed_neuron):
    select = ManagedSelect("mode", initial_value=None, options=["auto", "eco"])
    changes = []

    @select.when_changed()
    async def changed(change):
        changes.append(change)

    await select.set_value("auto")
    await select.set_value(None)
    assert select.value is None
    assert changes == [ValueChange(None, "auto"), ValueChange("auto", None)]
    assert (
        ManagedSelect("mode", initial_value="eco", options=select.options).value is None
    )


@pytest.mark.parametrize("value", ["invalid", "", 1, True])
async def test_select_rejects_invalid_values_without_side_effects(
    managed_neuron, value
):
    select = ManagedSelect("mode", initial_value="auto", options=["auto", "eco"])
    handler = AsyncMock()
    select.when_changed(handler)

    with pytest.raises(ValueError):
        await select.set_value(value)
    assert select.value == "auto"
    handler.assert_not_called()
    managed_neuron.state.save.assert_not_called()
    managed_neuron.hass.fire_event.assert_not_called()
    with pytest.raises(ValueError):
        ManagedSelect("invalid", initial_value=value, options=["auto", "eco"])


@pytest.mark.parametrize("options", [[], "auto", [""], ["auto", "auto"], [1], [None]])
def test_select_rejects_invalid_options(managed_neuron, options):
    with pytest.raises(ValueError):
        ManagedSelect("mode", initial_value=None, options=options)


def test_select_rejects_removed_restored_option_before_registration(managed_neuron):
    managed_neuron.state.managed_entity_states["select.mode"] = ManagedEntityState(
        "eco"
    )
    token = api._automation.set(object())
    try:
        with pytest.raises(ValueError):
            ManagedSelect("mode", initial_value="auto", options=["auto", "comfort"])
        assert "select.mode" not in api._managed_entities
    finally:
        api._automation.reset(token)


@pytest.mark.parametrize("initialized", [True, False])
async def test_select_routes_ha_commands_and_suppresses_disabled_handlers(
    managed_neuron, initialized
):
    select = ManagedSelect("mode", initial_value="auto", options=["auto", "eco"])
    other = ManagedSelect("other", initial_value="auto", options=select.options)
    handler = AsyncMock()
    select.when_changed(handler)
    managed_neuron.automations["example"] = SimpleNamespace(
        initialized=initialized,
        managed_entities={other.unique_id: other, select.unique_id: select},
    )
    message = bus.parse_message(
        integration_bus.SelectValueChanged(
            unique_id=select.unique_id, value="eco"
        ).model_dump()
    )
    await managed_neuron.managed_entities.handle_message(message)
    assert select.value == "eco"
    assert other.value == "auto"
    assert managed_neuron.state.managed_entity_states[select.unique_id].value == "eco"
    assert handler.await_count == int(initialized)


async def test_select_full_update_preserves_option_order(managed_neuron):
    select = ManagedSelect(
        "mode", initial_value="auto", options=["auto", "eco", "comfort"]
    )
    managed_neuron.managed_entities._managed_entities[select.unique_id] = select
    await managed_neuron.managed_entities.emit_full_update()
    message = integration_bus.parse_message(
        managed_neuron.hass.fire_event.call_args.kwargs["data"]
    )
    assert message.managed_selects == [
        select_payload(friendly_name="mode", automation=None)
    ]


def test_select_command_rejects_non_string_payload():
    with pytest.raises(ValidationError):
        bus.parse_message(
            {"type": "select-value-changed", "unique_id": "select.mode", "value": 1}
        )


def test_full_update_without_selects_is_supported():
    message = full_update().model_dump()
    del message["managed_selects"]
    assert integration_bus.parse_message(message).managed_selects == []


async def test_ha_select_updates_options_and_emits_commands(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    events = []

    async def record(event):
        events.append(event.data)

    hass.bus.async_listen("neuron", record)
    select = HassSelect(hass, select_payload())
    select.async_write_ha_state = Mock()
    await select.async_select_option("eco")
    await select.async_select_option("eco")
    await hass.async_block_till_done()
    assert select.current_option == "eco"
    assert events == [
        integration_bus.SelectValueChanged(
            unique_id="select.mode", value="eco"
        ).model_dump()
    ]
    await select.event_listener(
        Event(
            "neuron", bus.SetValue(unique_id="select.other", value="auto").model_dump()
        )
    )
    assert select.current_option == "eco"
    await select.event_listener(
        Event("neuron", bus.SetValue(unique_id="select.mode", value=None).model_dump())
    )
    assert select.current_option is None

    updated = select_payload(value="boost", options=["auto", "boost"])
    await select.event_listener(
        Event(
            "neuron",
            full_update(
                managed_selects=[bus.ManagedSelect(**updated.model_dump())]
            ).model_dump(),
        )
    )
    assert select.current_option == "boost"
    assert select.options == ["auto", "boost"]
    with pytest.raises(ValueError):
        await select.async_select_option("eco")
    assert select.current_option == "boost"


async def test_ha_select_registration_and_listener_cleanup(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    add = Mock()
    context = NeuronIntegrationContext(hass, add_select_entities=add)
    context.add_selects([select_payload()])
    context.add_selects([select_payload()])
    assert len(context.selects) == 1
    add.assert_called_once()

    select = context.selects[0]
    select.async_write_ha_state = Mock()
    await select.async_added_to_hass()
    hass.bus.async_fire(
        "neuron", bus.SetValue(unique_id="select.mode", value="eco").model_dump()
    )
    await hass.async_block_till_done()
    assert select.current_option == "eco"
    select._call_on_remove_callbacks()
    hass.bus.async_fire(
        "neuron", bus.SetValue(unique_id="select.mode", value="comfort").model_dump()
    )
    await hass.async_block_till_done()
    assert select.current_option == "eco"
