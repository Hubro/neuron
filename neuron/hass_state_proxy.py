import asyncio
from functools import cached_property, reduce
from types import MappingProxyType
from typing import Any, Hashable, Iterable

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
    _stop: asyncio.Event

    @cached_property
    def watched_entities(self) -> set[str]:
        return reduce(
            lambda x, y: x | y,
            self._clients.values(),
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
        self._stop = asyncio.Event()

    async def run(self):
        """The main work loop of the state proxy"""

        new_message = self.hass.messages.on_new_message.flag()
        reconnected = self.hass.on_reconnect.event()
        stop = self._stop

        try:
            LOG.info("Home Assistant state proxy started")

            while True:
                new_message.clear()

                for sub_id in self._subs:
                    if messages := self.hass.messages.pop(sub_id, []):
                        for msg in messages:
                            self._process_state_update(msg)

                # Now we wait for new messages...
                async with wait_event(new_message, reconnected, stop) as event:
                    if event is new_message:
                        pass

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

                        await self._establish_subscriptions()

                    if event is stop:
                        LOG.info("Got exit signal, clearing subscriptions")

                        for sub_id in self._subs:
                            await self.hass.unsubscribe(sub_id)

                        self._subs.clear()
                        self._clients.clear()

                        break

        except asyncio.CancelledError:
            return

    async def add(self, client: Hashable, entities: Iterable[str]):
        entities = set(entities)

        self._clients.setdefault(client, set()).update(entities)

        bust_cached_props(self, "watched_entities")

        for entity_id in self.watched_entities:
            self._state_readiness.setdefault(entity_id, asyncio.Event())

        await self._establish_subscriptions()

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

        LOG.info("Establishing state subscriptions")

        subscribed_entities = reduce(
            lambda x, y: x | y,
            self._subs.values(),
            set(),
        )
        entities_without_subs = self.watched_entities - subscribed_entities

        if not entities_without_subs:
            return

        LOG.debug(
            "State subscriptions missing for entities: %r", list(entities_without_subs)
        )

        sub_id = await self.hass.subscribe_to_entities(entities_without_subs)

        self._subs[sub_id] = entities_without_subs

    async def _prune_subscriptions(self):
        """Unsubscribes from any states that are no longer watched"""

        LOG.debug("Pruning subscriptions")

        for sub_id, entity_ids in self._subs.items():
            if not (self.watched_entities & entity_ids):
                LOG.debug(
                    "None of the entities of sub %r are being watched (%r), unsubscribing",
                    sub_id,
                    list(entity_ids),
                )
                await self.hass.unsubscribe(sub_id)

    def _process_state_update(self, state_event: dict[str, Any]):
        LOG.trace("Processing state update event: %r", state_event)

        if initial_states := state_event["event"].get("a"):
            for entity_id, state_object in initial_states.items():
                if entity_id not in self.watched_entities:
                    LOG.error(f"Got unexpected entity state update for {entity_id!r}")
                    continue

                self._states[entity_id] = state_object["s"]
                self._attributes[entity_id] = state_object["a"]

                LOG.debug(
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

        # _clients: dict[Hashable, set[str]]
        # _subs: dict[int, set[str]]  # subscription ID -> associated entity IDs
        # _states: dict[str, str]  # entity_id -> state
        # _attributes: dict[str, dict[str, Any]]  # entity_id -> asstibutes
        # _state_readiness: dict[str, asyncio.Event]
        # _task: asyncio.Task | None
        # _stop: asyncio.Event
