from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import orjson
import websockets

from .util import stringify

LOG = logging.getLogger(__name__)


class HASS:
    """Handles communication with Home Assistant using WebSockets

    NB: "connect" must be called before any other function.
    """

    hass_address: str
    token: str
    messages: Messages
    _ws: websockets.ClientConnection | None
    _handler_task_started: bool

    def __init__(self, hass_address: str, token: str) -> None:
        self.hass_address = hass_address
        self.token = token
        self.messages = Messages()
        self._ws = None
        self._handler_task_started = False

    @property
    def ws(self) -> websockets.ClientConnection:
        if self._ws is None:
            raise RuntimeError("Tried to access WebSocket connection before connecting")

        return self._ws

    async def connect(self):
        LOG.info("Connecting to Home Assistant")

        uri = f"ws://{self.hass_address}/api/websocket"
        LOG.debug("Opening Websocket connection: %s", uri)
        self._ws = await websockets.connect(uri=uri)

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

    async def message_handler_task(self):
        """Async task for accepting and distributing messages from HASS"""

        self._handler_task_started = True

        try:
            async for msg in self.ws:
                message = orjson.loads(msg)
                LOG.debug("Got message from Home Assistant: %r", message)

                self.messages.add(message)
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
                    await self.messages

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
    _event: asyncio.Event

    def __init__(self) -> None:
        self._msg_id = 0
        self._cache = {}
        self._event = asyncio.Event()

    def __getitem__(self, id: int) -> list[dict[str, Any]]:
        return self._cache[id]

    def get[T](self, id: int, default: T = None) -> list[dict[str, Any]] | T:
        return self._cache.get(id, default)

    def pop[T](self, id: int, default: T = None) -> list[dict[str, Any]] | T:
        return self._cache.pop(id, default)

    def __await__(self):
        """Returns when new messages are available"""
        return self._event.wait().__await__()

    def add(self, msg: dict[str, Any]):
        id = cast(int, msg["id"])
        self._cache.setdefault(id, []).append(msg)
        self.dingding()

    def next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def dingding(self):
        """Notifies waiting coroutines that new messages have arrived"""
        self._event.set()
        self._event.clear()
