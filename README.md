
# Neuron

Platform for writing your Home Assistant automations using Python.

Boasts:

- Simple, declarative, delightful API
- Fully typed
- Fully async
- Snappy and responsive

## Examples

Controlling a light with a motion sensor:

```python
from neuron import on_state_change, turn_off, turn_on


@on_state_change(
    "binary_sensor.motion_sensor_kitchen_occupancy",
    from_state="off",
    to_state="on",
)
async def on_motion():
    await turn_on(
        "light.kitchen_counter",
        brightness_pct=75,
        transition=0.2,
    )


@on_state_change(
    "binary_sensor.motion_sensor_kitchen_occupancy",
    from_state="on",
    to_state="off",
    duration=120,
)
async def on_motion_cleared():
    await turn_off("light.kitchen_counter")
```

Controlling lights and curtains with a Philips Hue remote:

```python
from neuron import Entity, NeuronLogger, on_event

remote = "1f770b3579b1a8bbca137902459f80a5"
curtains = Entity("cover.living_room_curtains")
living_room_lights = Entity("light.living_room_lights")


@on_event("zha_event", device_id=remote, command="up_press")
async def remote_open_curtain(log: NeuronLogger):
    if curtains.state == "opening":
        log.info("Curtains are opening, stopping")
        await curtains.stop_cover()
    else:
        log.info("Opening curtains")
        await curtains.open_cover()


@on_event("zha_event", device_id=remote, command="down_press")
async def remote_close_curtain(log: NeuronLogger):
    if curtains.state == "closing":
        log.info("Curtains are closing, stopping")
        await curtains.stop_cover()
    else:
        log.info("Closing curtains")
        await curtains.close_cover()


@on_event("zha_event", device_id=remote, command="off_press")
async def remote_lights_off(log: NeuronLogger):
    log.info("Turning off the lights")
    await living_room_lights.turn_off()


@on_event("zha_event", device_id=remote, command="on_press")
async def remote_lights_on(log: NeuronLogger):
    log.info("Turning the lights back on")
    await living_room_lights.turn_on()
```


## TODO

- [ ] Add an API for managing async tasks created by automations (error logging, timeouts)
- [ ] Override `__import__` when loading automations to track dependencies
    - Reload automations automatically when local dependencies change
- [ ] Add a simple web server for displaying the internal state of Neuron for debugging
