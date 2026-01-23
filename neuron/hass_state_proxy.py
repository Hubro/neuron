import asyncio
from functools import cached_property, reduce
from time import perf_counter
from types import MappingProxyType
from typing import Any, Hashable, Iterable

from .hass import HASS, State
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
    _task: asyncio.Task | None
    _lock: asyncio.Lock

    _stop: asyncio.Event
    """Signals graceful shutdown"""

    # Cache for full dump of all Home Assistant states. This lets watched
    # entities added in rapid succession use the same state dump.
    _hass_states_dump: dict[str, State] | None = None
    _hass_states_dump_ts: float | None = None

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
        self._task = None
        self._lock = asyncio.Lock()
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
                async with wait_event(new_message, reconnected, stop) as event:
                    if event is new_message:
                        continue

                    if event is reconnected:
                        LOG.info("Reconnected to Home Assistant")

                        # Clear all state related to previous HASS connection
                        self._subs = {}
                        self._states = {}
                        self._attributes = {}
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

            hass_states = await self._hass_states()

            for entity in new_entities:
                self._states[entity] = hass_states[entity]["state"]
                self._attributes[entity] = hass_states[entity]["attributes"]
                LOG.info(
                    "Set initial state for %r: %r | attributes=%r",
                    entity,
                    self._states[entity],
                    self._attributes[entity],
                )

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

    def get_state(self, entity_id: str) -> str:
        if entity_id not in self.watched_entities:
            raise KeyError(f"Entity is not watched: {entity_id!r}")

        return self._states[entity_id]

    def get_attributes(self, entity_id: str) -> MappingProxyType[str, Any]:
        if entity_id not in self.watched_entities:
            raise KeyError(f"Entity is not watched: {entity_id!r}")

        return MappingProxyType(self._attributes[entity_id])

    async def _hass_states(self) -> dict[str, State]:
        """Returns all states from HASS

        This function is cached and can safely be called repeatedly.
        """

        now = perf_counter()
        ts = self._hass_states_dump_ts
        hass_cache_timeout = 10  # seconds

        if ts and (now - ts) < hass_cache_timeout:
            assert self._hass_states_dump is not None
            return self._hass_states_dump

        LOG.info("Fetching all states from Home Assistant")
        dump = await self.hass.get_all_states()
        self._hass_states_dump = {state["entity_id"]: state for state in dump}
        self._hass_states_dump_ts = perf_counter()

        return self._hass_states_dump

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

            sub_id = await self.hass.subscribe_to_trigger(
                {"platform": "state", "entity_id": sorted(entities_without_subs)}
            )

            self._subs[sub_id] = entities_without_subs
            bust_cached_props(self, "subscribed_entities")

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

    def _process_state_update(self, event: dict[str, Any]):
        LOG.debug("Processing state update event", extra={"data": event})

        trigger = event["event"]["variables"]["trigger"]
        assert trigger["platform"] == "state"

        entity_id = trigger["entity_id"]
        state = self._states[entity_id] = trigger["to_state"]["state"]
        attrs = self._attributes[entity_id] = trigger["to_state"]["attributes"]

        LOG.trace(
            "Got state update for entity %r: %r | attributes=%r",
            entity_id,
            state,
            attrs,
        )

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
        }
