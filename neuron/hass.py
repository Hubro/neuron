from __future__ import annotations

import asyncio
from typing import Any, cast

import orjson
import websockets

from neuron.logging import get_logger

from .util import stringify

LOG = get_logger(__name__)


class HASS:
    """Handles communication with Home Assistant using WebSockets

    NB: "connect" must be called before any other function.
    """

    websocket_uri: str
    token: str
    messages: Messages
    _ws: websockets.ClientConnection | None
    _handler_task_started: bool
    _reconnected_events: list[asyncio.Event]
    _new_message_event: asyncio.Event

    def __init__(self, websocket_uri: str, token: str) -> None:
        self.websocket_uri = websocket_uri
        self.token = token
        self.messages = Messages()
        self._ws = None
        self._handler_task_started = False
        self._reconnected_events = []
        self._new_message_event = self.messages.new_message_event()

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

        for event in self._reconnected_events:
            event.set()
            event.clear()

    def reconnected_event(self) -> asyncio.Event:
        event = asyncio.Event()
        self._reconnected_events.append(event)
        return event

    async def message_handler_task(self):
        """Async task for accepting and distributing messages from HASS"""

        async def process_messages():
            while True:
                self._handler_task_started = True

                try:
                    async for msg in self.ws:
                        message = orjson.loads(msg)
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
                if response := self.messages.pop(id, None):
                    assert len(response) == 1
                    response = response[0]
                    break
                else:
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

    async def subscribe_to_event(self, event: str | None = None) -> int:
        raise NotImplementedError()

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

    async def unsubscribe(self, subscription_id: int):
        response = await self.message(
            {"type": "unsubscribe_events", "subscription": subscription_id}
        )
        assert response["success"]


class Messages:
    """Data strucure for holding and distributing messages from Home Assistant"""

    _msg_id: int
    _cache: dict[int, list[dict[str, Any]]]
    _new_message_events: list[asyncio.Event]

    def __init__(self) -> None:
        self._msg_id = 0
        self._cache = {}
        self._new_message_events = []

    def __getitem__(self, id: int) -> list[dict[str, Any]]:
        return self._cache[id]

    def get[T](self, id: int, default: T = None) -> list[dict[str, Any]] | T:
        return self._cache.get(id, default)

    def pop[T](self, id: int, default: T = None) -> list[dict[str, Any]] | T:
        return self._cache.pop(id, default)

    def add(self, msg: dict[str, Any]):
        id = cast(int, msg["id"])
        self._cache.setdefault(id, []).append(msg)
        self.dingding()

    def next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def dingding(self):
        """Notifies waiting coroutines that new messages have arrived"""
        for event in self._new_message_events:
            event.set()
            event.clear()

    def reset(self):
        """Resets the instance to its initial state, ready for a new HASS connection"""
        self._msg_id = 0
        self._cache.clear()

    def new_message_event(self) -> asyncio.Event:
        event = asyncio.Event()
        self._new_message_events.append(event)
        return event
