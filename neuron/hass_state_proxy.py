import asyncio
from functools import cached_property, reduce
from types import MappingProxyType
from typing import Any, Hashable, Iterable

from neuron.event_emitter import EventEmitter

from .hass import HASS
from .logging import get_logger
from .util import bust_cached_props, wait_event

LOG = get_logger(__name__)


class HASSStateProxy:
    """Component for providing an up-to-date view of HASS states

    Uses the "subscribe_entities" command to receive notifications from Home
    Assistant any time a watched entity's state changes. Keeps track of
    watched entities and subscriptions internally and exposes a simple API.

    Keeping track of entity states centrally avoids duplicate entity
    subscriptions from every automation.
    """

    hass: HASS

    _clients: dict[Hashable, set[str]]
    _subs: dict[int, set[str]]  # subscription ID -> associated entity IDs
    _states: dict[str, str]  # entity_id -> state
    _attributes: dict[str, dict[str, Any]]  # entity_id -> asstibutes
    _state_readiness: dict[str, asyncio.Event]
    _task: asyncio.Task | None
    _new_sub: EventEmitter
    _lock: asyncio.Lock

    _stop: asyncio.Event
    """Signals graceful shutdown"""

    @cached_property
    def watched_entities(self) -> set[str]:
        return reduce(
            lambda x, y: x | y,
            self._clients.values(),
            set(),
        )

    @property
    def subscribed_entities(self) -> set[str]:
        return reduce(
            lambda x, y: x | y,
            self._subs.values(),
            set(),
        )

    def __init__(self, hass: HASS) -> None:
        self.hass = hass

        self._clients = {}
        self._subs = {}
        self._states = {}
        self._attributes = {}
        self._state_readiness = {}
        self._task = None
        self._new_sub = EventEmitter()
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()

    async def run(self):
        """The main work loop of the state proxy"""

        new_message = self.hass.messages.on_new_message.flag()
        reconnected = self.hass.on_reconnect.event()
        new_sub = self._new_sub.flag()
        stop = self._stop

        try:
            LOG.info("Home Assistant state proxy started")

            while True:
                new_message.clear()
                new_sub.clear()

                LOG.trace("Processing state subscriptions")
                for sub_id in self._subs:
                    if messages := self.hass.messages.pop(sub_id, []):
                        LOG.trace(
                            "State subscription %r: %r new message(s)",
                            sub_id,
                            len(messages),
                            extra={"data": messages},
                        )

                        for msg in messages:
                            self._process_state_update(msg)
                    else:
                        LOG.trace("State subscription %r: No new messages", sub_id)

                # Now we wait for new messages...
                async with wait_event(new_message, new_sub, reconnected, stop) as event:
                    if event is new_message or event is new_sub:
                        continue

                    if event is reconnected:
                        LOG.info("Reconnected to Home Assistant")

                        # Clear all state related to previous HASS connection
                        self._subs = {}
                        self._states = {}
                        self._attributes = {}
                        self._state_readiness = {
                            entity_id: asyncio.Event()
                            for entity_id in self.watched_entities
                        }
                        bust_cached_props(self, "subscribed_entities")

                        await self._establish_subscriptions()

                    if event is stop:
                        LOG.info("Got exit signal, clearing subscriptions")

                        for sub_id in self._subs:
                            await self.hass.unsubscribe(sub_id)

                        break

        except asyncio.CancelledError:
            return

    async def add(self, client: Hashable, entities: Iterable[str]):
        entities = set(entities)
        new_entities = entities - self.watched_entities

        LOG.info("Adding client %r with entities: %r", client, entities)

        self._clients.setdefault(client, set()).update(entities)

        if new_entities:
            LOG.info("New entities encountered: %r", new_entities)

            bust_cached_props(self, "watched_entities")

            for entity_id in new_entities:
                self._state_readiness[entity_id] = asyncio.Event()

                # The entity might already be part of another subscription, in
                # which case we can immediately mark it as ready
                if entity_id in self.subscribed_entities:
                    self._state_readiness[entity_id].set()

            await self._establish_subscriptions()
        else:
            LOG.info("No new entities encountered")

    async def remove(self, client: Hashable):
        LOG.info("Removing client from state proxy: %r", client)

        before = self.watched_entities

        if client in self._clients:
            self._clients.pop(client)

        bust_cached_props(self, "watched_entities")
        after = self.watched_entities

        if removed := (before - after):
            # NOTE: Entities may still be getting updates if any other entities
            # in the same subscription are still watched, but that shouldn't be
            # a problem.
            LOG.info("Stopped watching entities: %r", list(removed))

        await self._prune_subscriptions()

    def stop(self):
        """Signals the state proxy to exit gracefully"""
        self._stop.set()

    async def wait_for_states(self, entity_ids: Iterable[str], timeout=3):
        """Returns when all the given entity_ids have a state value"""

        entity_ids = set(entity_ids)

        if unknown_entities := (entity_ids - self.watched_entities):
            raise ValueError(f"Unknown entities (not watched): {unknown_entities!r}")

        for entity_id in entity_ids:
            try:
                async with asyncio.timeout(timeout):
                    await self._state_readiness[entity_id].wait()
            except TimeoutError as e:
                stateless_entities = entity_ids - set(self._states.keys())

                raise ValueError(
                    f"Timed out waiting for entities {list(stateless_entities)!r}, "
                    f"perhaps they don't exist?"
                ) from e

    def get_state(self, entity_id: str) -> str:
        if entity_id not in self.watched_entities:
            raise KeyError(f"Entity is not watched: {entity_id!r}")
        elif not self._state_readiness[entity_id].is_set():
            raise ValueError(f"Entity is not ready: {entity_id!r}")

        return self._states[entity_id]

    def get_attributes(self, entity_id: str) -> MappingProxyType[str, Any]:
        if entity_id not in self.watched_entities:
            raise KeyError(f"Entity is not watched: {entity_id!r}")
        elif not self._state_readiness[entity_id].is_set():
            raise ValueError(f"Entity is not ready: {entity_id!r}")

        return MappingProxyType(self._attributes[entity_id])

    async def _establish_subscriptions(self):
        """Subscribes to changes for any watched entities that don't yet have one"""

        async with self._lock:
            LOG.info(
                "Establishing state subscriptions",
                extra={
                    "data": {
                        "watched_entities": self.watched_entities,
                        "subscribed_entities": self.subscribed_entities,
                    }
                },
            )

            entities_without_subs = self.watched_entities - self.subscribed_entities

            if not entities_without_subs:
                LOG.info("All entities already have a subscription")
                return

            LOG.info("Subscribing to entities: %r", list(entities_without_subs))

            sub_id = await self.hass.subscribe_to_entities(entities_without_subs)

            self._subs[sub_id] = entities_without_subs
            bust_cached_props(self, "subscribed_entities")

        # The initial state may already have arrived during the above "await",
        # so we need to force another processing cycle
        self._new_sub.emit()

    async def _prune_subscriptions(self):
        """Unsubscribes from any states that are no longer watched"""

        LOG.info("Pruning subscriptions")

        async with self._lock:
            for sub_id, entity_ids in list(self._subs.items()):
                if not (self.watched_entities & entity_ids):
                    LOG.info(
                        "None of the entities of sub %r are being watched (%r), unsubscribing",
                        sub_id,
                        list(entity_ids),
                    )
                    del self._subs[sub_id]
                    bust_cached_props(self, "subscribed_entities")
                    await self.hass.unsubscribe(sub_id)

    def _process_state_update(self, state_event: dict[str, Any]):
        LOG.debug("Processing state update event", extra={"data": state_event})

        if initial_states := state_event["event"].get("a"):
            for entity_id, state_object in initial_states.items():
                if entity_id not in self.watched_entities:
                    LOG.error(f"Got unexpected entity state update for {entity_id!r}")
                    continue

                self._states[entity_id] = state_object["s"]
                self._attributes[entity_id] = state_object["a"]

                LOG.info(
                    "Got initial state for entity %r | state=%r attributes=%r",
                    entity_id,
                    state_object["s"],
                    state_object["a"],
                )

                # The entity state now has a value, any automations waiting for it
                # can proceed
                self._state_readiness[entity_id].set()

        elif changed_state := state_event["event"].get("c"):
            for entity_id, state_object in changed_state.items():
                if "+" in state_object:
                    diff = state_object["+"]

                    if "s" in diff:
                        self._states[entity_id] = diff["s"]
                        LOG.debug("Entity %r state updated: %r", entity_id, diff["s"])

                    if "a" in diff:
                        self._attributes[entity_id].update(diff["a"])
                        LOG.debug(
                            "Entity %r attributes updated: %r", entity_id, diff["a"]
                        )

                else:
                    LOG.error(f"Unexpected state update message: {state_event!r}")
                    continue

        else:
            raise RuntimeError(f"Unexpected entity state event: {state_event!r}")

    def _dump_state(self) -> dict[str, Any]:
        """Dumps internal state for debugging"""

        return {
            "clients": {
                str(repr(client)): entities
                for client, entities in self._clients.items()
            },
            "subscriptions": {
                str(sub_id): entity_ids for sub_id, entity_ids in self._subs.items()
            },
            "states": self._states,
            "attributes": self._attributes,
            "state_readiness": self._state_readiness,
        }
