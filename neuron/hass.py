from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

import orjson
import websockets

from .event_emitter import EventEmitter
from .logging import get_logger
from .util import stringify

if TYPE_CHECKING:
    from .api import Entity

LOG = get_logger(__name__)


class HASS:
    """Handles communication with Home Assistant using WebSockets

    NB: "connect" must be called before any other function.
    """

    websocket_uri: str
    token: str
    messages: Messages
    on_reconnect: EventEmitter
    on_new_message: EventEmitter

    def __init__(self, websocket_uri: str, token: str) -> None:
        self.websocket_uri = websocket_uri
        self.token = token
        self.messages = Messages()
        self.on_reconnect = EventEmitter()
        self.on_new_message = EventEmitter()

        self._ws: websockets.ClientConnection | None = None
        self._handler_task_started = False
        self._new_message_event = self.messages.on_new_message.event()

    @property
    def ws(self) -> websockets.ClientConnection:
        if self._ws is None:
            raise RuntimeError("Tried to access WebSocket connection before connecting")

        return self._ws

    async def connect(self):
        LOG.info("Connecting to Home Assistant")

        LOG.debug("Opening Websocket connection: %s", self.websocket_uri)
        self._ws = await websockets.connect(uri=self.websocket_uri)

        LOG.debug("Waiting for auth request...")
        request = orjson.loads(await self._ws.recv())
        assert request["type"] == "auth_required"

        LOG.debug("Sending auth message...")
        response = await self.message(
            {
                "type": "auth",
                "access_token": self.token,
            }
        )

        if response["type"] == "auth_ok":
            LOG.info("Home Assistant authentication successful")
        elif response["type"] == "auth_invalid":
            raise RuntimeError("Home Assistant authentication failed, check token")
        else:
            raise RuntimeError("Unexpected response: %r", response)

        LOG.debug("Enabling coalesced messages feature")
        response = await self.message(
            {
                "type": "supported_features",
                "features": {"coalesce_messages": 1},
            }
        )
        assert response["success"]

    async def disconnect(self):
        await self.ws.close()
        await self.ws.wait_closed()

    async def reconnect(self):
        self._handler_task_started = False
        self.messages.reset()
        LOG.info("Attempting to reconnect to Home Assistant...")

        wait = 1
        max_wait = 10

        while True:
            try:
                await self.connect()
                break
            except Exception as e:
                LOG.error("Failed to reconnect to Home Assistant: %s", e)
                await asyncio.sleep(wait)

                wait = min(wait * 2, max_wait)

        LOG.info("Connection re-established!")

        self.on_reconnect.emit()

    async def message_handler_task(self):
        """Async task for accepting and distributing messages from HASS"""

        async def process_messages():
            while True:
                self._handler_task_started = True

                try:
                    async for msg in self.ws:
                        messages = orjson.loads(msg)

                        if not isinstance(messages, list):
                            messages = [messages]

                        for message in messages:
                            LOG.debug("Got message from Home Assistant: %r", message)
                            self.messages.add(message)

                except websockets.ConnectionClosed as e:
                    LOG.error("Lost connection with Home Assistant: %s", e)
                    await self.reconnect()

        try:
            await process_messages()
        except asyncio.CancelledError:
            return

    async def message(self, msg: dict[str, Any]) -> Any:
        """Sends a message to Home Assistant and returns the response"""

        msg = msg.copy()

        # The initial auth message should have no message ID
        if msg["type"] == "auth":
            id = 0
        else:
            id = self.messages.next_id()
            msg["id"] = id

            # Also let's not debug log the access token of the auth message
            LOG.debug("Sending message to Home Assistant: %r", msg)

        await self.ws.send(orjson.dumps(msg), text=True)

        if not self._handler_task_started:
            # If the message handler hasn't been started yet, fetch the
            # response manually
            response = orjson.loads(await self.ws.recv())
            LOG.debug("Received response: %r", response)

            if "id" in msg:
                assert response["id"] == msg["id"]
        else:
            # If the message handler is running, wait for the response to show
            # up in the message cache
            while True:
                response = self.messages.pop_message(id)

                if response:
                    break

                await self._new_message_event.wait()

        return response

    async def perform_action(
        self,
        domain: str,
        name: str,
        /,
        target: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ):
        message_body: dict[str, Any] = {
            "type": "call_service",
            "domain": domain,
            # The WebSockets API still uses the outdated "service" terminology
            "service": name,
        }

        if data:
            message_body["service_data"] = data
        if target:
            message_body["target"] = target

        response = await self.message(message_body)

        if not response["success"]:
            LOG.error("Failed to perform action: %s", response["error"]["message"])
            return

    async def subscribe_to_events(self, event: str = "*") -> int:
        message = {"type": "subscribe_events"}

        if event != "*":
            message["event_type"] = event

        response = await self.message(message)
        id = response["id"]

        if not response["success"]:
            LOG.error(
                "Failed to subscribe to event %r: %s",
                event,
                response["error"]["message"],
            )

        LOG.debug("Subscribed to event (id=%d): %s", id, event)
        return id

    async def subscribe_to_trigger(self, trigger: dict[str, Any]) -> int:
        """Subscribes to a trigger and returns the Subscription"""

        key = stringify(trigger)

        response = await self.message(
            {
                "type": "subscribe_trigger",
                "trigger": trigger,
            }
        )
        id = response["id"]

        if not response["success"]:
            LOG.error(
                "Failed to subscribe to trigger %r: %s",
                key,
                response["error"]["message"],
            )

        LOG.debug("Subscribed to trigger (id=%d): %s", id, key)
        return id

    async def subscribe_to_entities(self, entities: list[Entity]) -> int:
        """Subscribes to one or more entities"""
        assert entities

        entity_ids = [entity.entity_id for entity in entities]
        response = await self.message(
            {
                "type": "subscribe_entities",
                "entity_ids": entity_ids,
            }
        )
        id = response["id"]

        if not response["success"]:
            LOG.error(
                "Failed to subscribe to entities %r: %s",
                entity_ids,
                response["error"]["message"],
            )

        LOG.debug("Subscribed to entities (id=%d): %s", id, entity_ids)
        return id

    async def unsubscribe(self, subscription_id: int):
        response = await self.message(
            {"type": "unsubscribe_events", "subscription": subscription_id}
        )
        assert response["success"]


class Messages:
    """Data strucure for holding and distributing messages from Home Assistant"""

    on_new_message: EventEmitter
    _msg_id: int
    _cache: dict[int, list[dict[str, Any]]]

    def __init__(self) -> None:
        self.on_new_message = EventEmitter()

        self._msg_id = 0
        self._cache = {}

    def __getitem__(self, id: int) -> list[dict[str, Any]]:
        return self._cache[id]

    def get[T](self, id: int, default: T = None) -> list[dict[str, Any]] | T:
        return self._cache.get(id, default)

    def pop[T](self, id: int, default: T = None) -> list[dict[str, Any]] | T:
        return self._cache.pop(id, default)

    def pop_message(self, id: int) -> dict[str, Any] | None:
        """Pops the oldest message with the given ID"""

        if id in self._cache:
            item = self._cache[id].pop(0)

            if not self._cache[id]:
                del self._cache[id]

            return item
        else:
            return None

    def add(self, msg: dict[str, Any]):
        id = cast(int, msg["id"])
        self._cache.setdefault(id, []).append(msg)

        self.on_new_message.emit()

    def next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def reset(self):
        """Resets the instance to its initial state, ready for a new HASS connection"""
        self._msg_id = 0
        self._cache.clear()
