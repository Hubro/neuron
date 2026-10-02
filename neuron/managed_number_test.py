from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import Event, HomeAssistant
from pydantic import ValidationError

from custom_components.neuron_integration import bus as integration_bus
from custom_components.neuron_integration.context import NeuronIntegrationContext
from custom_components.neuron_integration.number import ManagedNumber as HassNumber
from custom_components.neuron_integration.sensor import ManagedSensor as HassSensor
from neuron import (
    ManagedNumber,
    ManagedSensor,
    NumberDeviceClass,
    NumberMode,
    ValueChange,
    api,
    bus,
)
from neuron.state import ManagedEntityState, NeuronState


def number_payload(**kwargs):
    return integration_bus.ManagedNumber(
        **{
            "unique_id": "number.target",
            "friendly_name": "Target",
            "automation": "example",
            "value": 21,
            "min_value": 5,
            "max_value": 30,
            "step": 0.5,
            "unit_of_measurement": "°C",
            "device_class": NumberDeviceClass.TEMPERATURE,
            "mode": NumberMode.SLIDER,
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


async def test_number_changes_persist_and_call_handler_once(managed_neuron):
    number = ManagedNumber("target", initial_value=21)
    changes = []

    @number.when_changed
    async def changed(number, change, log):
        changes.append((number, change, log))

    await number.set_value(22.5)
    await number.set_value(22.5)

    assert number.value == 22.5
    assert changes == [(number, ValueChange(21, 22.5), api._l())]
    assert callable(changed)
    managed_neuron.state.save.assert_called_once()
    event = managed_neuron.hass.fire_event.call_args.kwargs["data"]
    assert bus.parse_message(event) == bus.SetValue(
        unique_id="number.target", value=22.5
    )
    managed_neuron.state = NeuronState.from_dict(managed_neuron.state._dump_state())
    assert ManagedNumber("target", initial_value=0).value == 22.5


async def test_number_unknown_and_unrounded_values(managed_neuron):
    number = ManagedNumber("target", initial_value=None, step=1)
    handler = AsyncMock()
    number.when_changed()(handler)

    await number.set_value(1.25)
    assert number.value == 1.25
    await number.set_value(None)
    assert number.value is None
    assert handler.await_count == 2
    assert ManagedNumber("target", initial_value=0).value is None


@pytest.mark.parametrize("value", [-1, 101, float("nan"), float("inf"), True, "5"])
async def test_number_rejects_invalid_values_without_side_effects(
    managed_neuron, value
):
    number = ManagedNumber("target", initial_value=50)
    handler = AsyncMock()
    number.when_changed(handler)

    with pytest.raises(ValueError):
        await number.set_value(value)
    assert number.value == 50
    handler.assert_not_called()
    managed_neuron.state.save.assert_not_called()
    managed_neuron.hass.fire_event.assert_not_called()

    with pytest.raises(ValueError):
        ManagedNumber("invalid", initial_value=value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_value": 100},
        {"max_value": -1},
        {"min_value": float("nan")},
        {"max_value": float("inf")},
        {"min_value": True},
        {"step": 0},
        {"step": -1},
        {"step": float("nan")},
        {"step": True},
    ],
)
def test_number_rejects_invalid_configuration(managed_neuron, kwargs):
    with pytest.raises(ValueError):
        ManagedNumber("target", initial_value=None, **kwargs)


def test_number_rejects_invalid_restored_value_before_registration(managed_neuron):
    managed_neuron.state.managed_entity_states["number.target"] = ManagedEntityState(50)
    token = api._automation.set(object())
    try:
        with pytest.raises(ValueError):
            ManagedNumber("target", initial_value=20, max_value=30)
        assert "number.target" not in api._managed_entities
    finally:
        api._automation.reset(token)


@pytest.mark.parametrize("initialized", [True, False])
async def test_number_routes_ha_commands_and_suppresses_disabled_handlers(
    managed_neuron, initialized
):
    number = ManagedNumber("target", initial_value=21)
    other = ManagedNumber("other", initial_value=1)
    handler = AsyncMock()
    number.when_changed(handler)
    managed_neuron.automations["example"] = SimpleNamespace(
        initialized=initialized,
        managed_entities={other.unique_id: other, number.unique_id: number},
    )

    message = bus.parse_message(
        integration_bus.NumberValueChanged(
            unique_id=number.unique_id, value=23
        ).model_dump()
    )
    await managed_neuron.managed_entities.handle_message(message)
    assert number.value == 23
    assert other.value == 1
    assert managed_neuron.state.managed_entity_states[number.unique_id].value == 23
    assert handler.await_count == int(initialized)


async def test_number_full_update_includes_metadata_and_sensor_units(managed_neuron):
    number = ManagedNumber(
        "target",
        initial_value=21,
        min_value=5,
        max_value=30,
        step=0.5,
        unit_of_measurement="°C",
        device_class=NumberDeviceClass.TEMPERATURE,
        mode=NumberMode.SLIDER,
    )
    sensor = ManagedSensor("measured", initial_value=20, unit_of_measurement="°C")
    managed_neuron.managed_entities._managed_entities.update(
        {number.unique_id: number, sensor.unique_id: sensor}
    )
    await managed_neuron.managed_entities.emit_full_update()

    message = integration_bus.parse_message(
        managed_neuron.hass.fire_event.call_args.kwargs["data"]
    )
    assert message.managed_numbers == [
        number_payload(friendly_name="target", automation=None)
    ]
    assert message.managed_sensors[0].unit_of_measurement == "°C"


@pytest.mark.parametrize("value", [True, "3", float("nan"), float("inf"), None])
def test_number_command_rejects_invalid_payload(value):
    with pytest.raises(ValidationError):
        bus.parse_message(
            {
                "type": "number-value-changed",
                "unique_id": "number.target",
                "value": value,
            }
        )


def test_full_update_without_numbers_is_supported():
    message = full_update().model_dump()
    del message["managed_numbers"]
    assert integration_bus.parse_message(message).managed_numbers == []


async def test_ha_number_updates_and_emits_commands(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    events = []

    async def record(event):
        events.append(event.data)

    hass.bus.async_listen("neuron", record)
    number = HassNumber(hass, number_payload())
    number.async_write_ha_state = Mock()

    await number.async_set_native_value(22.5)
    await number.async_set_native_value(22.5)
    await hass.async_block_till_done()
    assert number.native_value == 22.5
    assert events == [
        integration_bus.NumberValueChanged(
            unique_id="number.target", value=22.5
        ).model_dump()
    ]
    await number.event_listener(
        Event("neuron", bus.SetValue(unique_id="number.other", value=20).model_dump())
    )
    assert number.native_value == 22.5
    await number.event_listener(
        Event(
            "neuron", bus.SetValue(unique_id="number.target", value=None).model_dump()
        )
    )
    assert number.native_value is None

    updated = number_payload(value=18, max_value=40, step=None, mode=NumberMode.BOX)
    await number.event_listener(
        Event(
            "neuron",
            full_update(
                managed_numbers=[bus.ManagedNumber(**updated.model_dump())]
            ).model_dump(),
        )
    )
    assert number.native_value == 18
    assert number.native_max_value == 40
    assert number.native_step is None
    assert number.mode == NumberMode.BOX
    assert number.native_unit_of_measurement == "°C"
    assert number.device_class == NumberDeviceClass.TEMPERATURE
    with pytest.raises(ValueError):
        await number.async_set_native_value(41)
    assert number.native_value == 18


async def test_ha_number_registration_and_listener_cleanup(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    add = Mock()
    context = NeuronIntegrationContext(hass, add_number_entities=add)
    context.add_numbers([number_payload()])
    context.add_numbers([number_payload()])
    assert len(context.numbers) == 1
    add.assert_called_once()

    number = context.numbers[0]
    number.async_write_ha_state = Mock()
    await number.async_added_to_hass()
    hass.bus.async_fire(
        "neuron", bus.SetValue(unique_id="number.target", value=22).model_dump()
    )
    await hass.async_block_till_done()
    assert number.native_value == 22
    number._call_on_remove_callbacks()
    hass.bus.async_fire(
        "neuron", bus.SetValue(unique_id="number.target", value=23).model_dump()
    )
    await hass.async_block_till_done()
    assert number.native_value == 22


async def test_sensor_unit_rename_reaches_home_assistant(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    sensor = HassSensor(hass, None, "sensor.measured", 20, "Measured", "°C")
    assert sensor.native_unit_of_measurement == "°C"
