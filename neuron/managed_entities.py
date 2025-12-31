# pyright: enableExperimentalFeatures=true
"""Module for handling managed entities through the Neuron companion integration"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Iterator, Self, cast

from neuron.state import ManagedEntityState
from neuron.util import NEURON_CORE, Clock, wait_event

from . import api, bus
from .logging import get_logger

LOG = get_logger(__name__)


if TYPE_CHECKING:
    from .core import Automation, Neuron


class ManagedEntities:
    """Handles managed entities"""

    neuron: Neuron
    _managed_entities: dict[str, api.ManagedEntity]

    _stop: asyncio.Event
    """Signals graceful shutdown"""

    def __init__(self, neuron: Neuron):
        self.neuron = neuron

        self._stop = asyncio.Event()
        self._managed_entities = {}

    async def run(self):
        """Main loop of the managed entities handler, should be run as a task"""

        LOG.info("Subscribing to messages from the Neuron integration")
        await self.neuron.subscribe(NEURON_CORE, self.handle_message_event, to="neuron")

        # Wait until all automations are loaded before proceeding, otherwise
        # the stats entities won't have the right states
        await self.neuron._ready.wait()

        self.create_core_managed_entities()

        # Send the integration a full update just in case. That way, we can
        # always be sure the integration is up-to-date after restarting the
        # Neuron addon.
        await self.emit_full_update()

        clock = Clock(1.0)
        clock.start()

        stop = self._stop
        tick = clock.event()

        try:
            while True:
                async with wait_event(stop, tick) as event:
                    if event is tick:
                        await self.refresh_core_entities()

                    elif event is stop:
                        LOG.info("Got exit signal, stopping gracefully")
                        await self.neuron.unsubscribe(
                            self.handle_message_event, "neuron"
                        )
                        return

        except asyncio.CancelledError:
            pass

    def create_core_managed_entities(self):
        """Creates the Neuron core managed entities and sets their initial states"""

        stats = NeuronCoreStats.resolve(self.neuron)

        for stat, value in asdict(stats).items():
            self._managed_entities[stat] = api.ManagedSensor(
                f"neuron_core_{stat}", initial_value=value
            )

        LOG.debug(
            "Created core managed entities: %r", list(self._managed_entities.values())
        )

    def create_automation_toggle_switch(self, automation: Automation):
        automation_enabled = api.ManagedSwitch(
            f"neuron_automation_{automation.name}_enabled",
            friendly_name=f"Neuron - {automation.name} - Enabled",
            initial_value=automation.enabled,
        )
        setattr(automation_enabled, "__automation", automation)
        self._set_managed_entity_value(automation_enabled.unique_id, automation.enabled)

        @automation_enabled.when_turned_on
        async def on():
            await self.neuron.enable_automation(automation)

        @automation_enabled.when_turned_off
        async def off():
            await self.neuron.disable_automation(automation)

        self._managed_entities[automation_enabled.name] = automation_enabled

    async def refresh_core_entities(self):
        """Refreshes the state of Neuron core's managed entities"""

        stats = NeuronCoreStats.resolve(self.neuron)

        for stat, value in asdict(stats).items():
            sensor = cast(api.ManagedSensor[int], self._managed_entities[stat])

            if sensor.value != value:
                LOG.info(
                    "Updating value of Neuron managed entity %r: %r",
                    sensor.unique_id,
                    value,
                )
                await sensor.set_value(value)

    async def handle_message_event(self, event_type: str, event: dict[str, Any]):
        """Event handler for messages from the Neuron integration"""

        assert event_type == "neuron"

        # Don't answer any messages from the integration until we're fully up
        # and running
        await self.neuron._ready.wait()

        try:
            message = bus.parse_message(event)
        except Exception:
            LOG.exception("Failed to parse Neuron integration message")
            return

        if isinstance(message, bus.NeuronCoreMessage):
            return  # Ignore our own messages

        LOG.trace("Got message from Neuron companion integration: %r", message)
        await self.handle_message(message)

    async def handle_message(self, message: bus.NeuronIntegrationMessage):
        match message:
            case bus.RequestingFullUpdate():
                await self.emit_full_update()

            case bus.RequestingInternalStateDump():
                await self.send(
                    bus.InternalStateDump(internal_state=self.neuron._dump_state())
                )

            case bus.SwitchTurnedOff() | bus.SwitchTurnedOn():
                new_value = isinstance(message, bus.SwitchTurnedOn)

                for automation, managed_entity in self.all_managed_entities():
                    match managed_entity:
                        case api.ManagedSwitch(unique_id=message.unique_id):
                            old_value = managed_entity.value

                            if new_value == old_value:
                                break

                            await managed_entity.set_value(
                                new_value,
                                suppress_handler=(
                                    automation and not automation.initialized
                                ),
                            )
                            break

            case bus.ButtonPressed():
                for automation, managed_entity in self.all_managed_entities():
                    match managed_entity:
                        case api.ManagedButton(unique_id=message.unique_id):
                            if automation and not automation.initialized:
                                return

                            await managed_entity.press()
                            break

            case other:
                raise RuntimeError(f"Unexpected integration message: {other!r}")

    def all_managed_entities(
        self,
    ) -> Iterator[tuple[Automation | None, api.ManagedEntity]]:
        for entity in self._managed_entities.values():
            yield (None, entity)

        for automation in self.neuron.automations.values():
            for entity in automation.managed_entities.values():
                yield (automation, entity)

    async def emit_full_update(self):
        """Emits a full state update of Neuron's managed entities"""

        LOG.info("Emitting full managed entity state update to Neuron integration")

        managed_switches = []
        managed_sensors = []
        managed_buttons = []

        for automation, entity in self.all_managed_entities():
            match entity:
                case api.ManagedSwitch():
                    managed_switches.append(
                        bus.ManagedSwitch(
                            unique_id=entity.unique_id,
                            friendly_name=entity.friendly_name or entity.name,
                            value=entity.value,
                            automation=automation.name if automation else None,
                        )
                    )

                case api.ManagedSensor():
                    managed_sensors.append(
                        bus.ManagedSensor(
                            unique_id=entity.unique_id,
                            friendly_name=entity.friendly_name or entity.name,
                            value=entity.value,
                            automation=automation.name if automation else None,
                            device_class=entity.device_class,
                            state_class=entity.state_class,
                            native_unit_of_measurement=entity.native_unit_of_measurement,
                            suggested_unit_of_measurement=entity.suggested_unit_of_measurement,
                        )
                    )

                case api.ManagedButton():
                    managed_buttons.append(
                        bus.ManagedButton(
                            unique_id=entity.unique_id,
                            friendly_name=entity.friendly_name,
                            automation=automation.name if automation else None,
                        )
                    )

        message = bus.FullUpdate(
            managed_switches=managed_switches,
            managed_sensors=managed_sensors,
            managed_buttons=managed_buttons,
        )

        await self.send(message)

    async def send(self, message: bus.NeuronCoreMessage):
        LOG.debug("Sending message to integration: %r", message)
        await self.neuron.hass.fire_event("neuron", data=message.model_dump())

    def _set_managed_entity_value(self, entity_id: str, value: Any):
        LOG.debug("Setting value of managed entity %r to %r", entity_id, value)

        state = self.neuron.state

        if x := state.managed_entity_states.get(entity_id):
            if x.value == value:
                return  # State unchanged

            x.value = value
        else:
            state.managed_entity_states[entity_id] = ManagedEntityState(value)

        state.save()

    async def _signal_managed_entity_value(self, entity_id: str):
        state = self.neuron.state.managed_entity_states.get(entity_id)
        assert state

        await self.send(bus.SetValue(unique_id=entity_id, value=state.value))

    async def set_value(self, entity_id: str, value: Any, skip_send=False):
        """Sets the managed entity value"""

        self._set_managed_entity_value(entity_id, value)

        if not skip_send:
            await self._signal_managed_entity_value(entity_id)


@dataclass(frozen=True)
class NeuronCoreStats:
    num_trigger_subscriptions: int
    num_event_subscriptions: int
    num_state_subscriptions: int
    num_automations: int

    @classmethod
    def resolve(cls, neuron: Neuron) -> Self:
        num_trigger_subscriptions = sum(
            1 for sub in neuron.subscriptions if isinstance(sub.subject, dict)
        )
        num_event_subscriptions = sum(
            1 for sub in neuron.subscriptions if isinstance(sub.subject, str)
        )

        return cls(
            num_trigger_subscriptions=num_trigger_subscriptions,
            num_event_subscriptions=num_event_subscriptions,
            num_state_subscriptions=len(neuron.hass_state_proxy._subs),
            num_automations=len(neuron.automations),
        )
