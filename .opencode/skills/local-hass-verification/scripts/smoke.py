from neuron import (
    ManagedNumber,
    ManagedSelect,
    ManagedSensor,
    NumberDeviceClass,
    NumberMode,
    on_event,
)

number = ManagedNumber(
    "smoke_number",
    initial_value=21,
    min_value=5,
    max_value=30,
    step=0.5,
    unit_of_measurement="°C",
    device_class=NumberDeviceClass.TEMPERATURE,
    mode=NumberMode.SLIDER,
)
select = ManagedSelect(
    "smoke_select", initial_value="auto", options=["auto", "eco", "comfort"]
)
number_observed = ManagedSensor("smoke_number_observed", initial_value=21)
select_observed = ManagedSensor("smoke_select_observed", initial_value="auto")
number_callbacks = ManagedSensor("smoke_number_callbacks", initial_value=0)
select_callbacks = ManagedSensor("smoke_select_callbacks", initial_value=0)
measured = ManagedSensor("smoke_measured", initial_value=20, unit_of_measurement="°C")


@number.when_changed
async def number_changed(change):
    await number_observed.set_value(change.to_value)
    count = number_callbacks.value
    assert count is not None
    await number_callbacks.set_value(count + 1)


@select.when_changed
async def select_changed(change):
    await select_observed.set_value(change.to_value)
    count = select_callbacks.value
    assert count is not None
    await select_callbacks.set_value(count + 1)


@on_event("neuron_smoke_set")
async def set_from_python(event):
    if "number" in event:
        await number.set_value(event["number"])
    if "select" in event:
        await select.set_value(event["select"])
