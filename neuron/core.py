from __future__ import annotations

import asyncio
import importlib
import json
import logging
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import websockets

import neuron.api

from .api import StateChange
from .config import Config, load_config
from .util import stringify

LOG = logging.getLogger(__name__)


class Neuron:
    automations: dict[str, Automation]
    config: Config
    hass_ws: websockets.ClientConnection
    tasks: list[asyncio.Task]

    msg_cache: dict[int, list[Any]]
    msg_id: int
    msg_event: asyncio.Event

    event_subscriptions: dict[str, int]  # dict[<event name>, <subscription_id>]
    trigger_subscriptions: dict[str, int]  # dict[<trigger (json)>, <subscription_id>]

    # Registered handler functions (subscription id -> (automation, handler))
    event_handlers: dict[int, list[tuple[Automation, Callable]]]

    def __init__(self) -> None:
        self.automations = {}
        self.config = load_config()
        self.tasks = []
        self.msg_cache = {}
        self.msg_id = 2
        self.msg_event = asyncio.Event()
        self.event_subscriptions = {}
        self.trigger_subscriptions = {}
        self.event_handlers = {}

    async def start(self):
        LOG.info("Starting Neuron!")

        await self.connect()
        self.tasks.append(asyncio.create_task(self.websocket_message_handler_task()))
        self.tasks.append(asyncio.create_task(self.event_subscription_handler_task()))

        neuron.api._reset()
        neuron.api._neuron = self  # Any API usage will now target this Neuron instance

        self.load_automations()
        await self.init_automations()

        try:
            done, pending = await asyncio.wait(
                self.tasks, return_when=asyncio.FIRST_COMPLETED
            )

            for task in done:
                LOG.fatal(
                    "Background task has exited unexpectedly!",
                    exc_info=task.exception(),
                )

            return
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            LOG.info("Shutting down gracefully")

            for task in self.tasks:
                if task.cancel():
                    await task

            LOG.debug("Closing Home Assistant Websocket connection")
            await self.hass_ws.close()
            await self.hass_ws.wait_closed()

            LOG.info("Bye!")

    async def websocket_message_handler_task(self):
        """Async task for accepting and distributing messages from HASS"""

        try:
            async for msg in self.hass_ws:
                message = json.loads(msg)
                self.msg_cache.setdefault(message["id"], []).append(message)

                LOG.debug("Got message from Home Assistant: %r", message)

                # Wake waiting tasks
                self.msg_event.set()
                self.msg_event.clear()
        except asyncio.CancelledError:
            return

    async def event_subscription_handler_task(self):
        """Async task for keeping track of subscriptions and calling handlers"""

        try:
            while True:
                for id in self.event_handlers.keys():
                    for msg in self.msg_cache.pop(id, []):
                        await self.dispatch_event(msg)

                await self.msg_event.wait()
        except asyncio.CancelledError:
            return

    async def dispatch_event(self, event_msg: dict[str, Any]):
        assert event_msg["type"] == "event"
        handlers = self.event_handlers.get(event_msg["id"])

        if not handlers:
            LOG.warning("Got event message with no subscribers: %r", event_msg)
            return

        is_event = "data" in event_msg["event"]
        is_trigger = "variables" in event_msg["event"]
        handler_kwargs: dict[str, Any] = {}

        if is_event:
            raise NotImplementedError()

        elif is_trigger:
            trigger = event_msg["event"]["variables"]["trigger"]
            platform = trigger["platform"]

            match platform:
                case "state":
                    handler_kwargs["change"] = StateChange.from_event_message(event_msg)
                case "time":
                    pass
                case _:
                    handler_kwargs["trigger"] = trigger

        else:
            raise RuntimeError("Unrecognized event message: %r", event_msg)

        for _, handler in handlers:
            await handler(**handler_kwargs)

    async def connect(self):
        LOG.info("Connecting to Home Assistant")

        uri = f"ws://{self.config.hass_api_url}/api/websocket"
        LOG.debug("Opening Websocket connection: %s", uri)
        self.hass_ws = await websockets.connect(uri=uri)

        LOG.debug("Waiting for auth request...")
        request = json.loads(await self.hass_ws.recv(decode=True))
        assert request["type"] == "auth_required"

        LOG.debug("Sending auth message...")
        await self.hass_ws.send(
            json.dumps(
                {
                    "type": "auth",
                    "access_token": self.config.hass_api_token,
                }
            )
        )

        LOG.debug("Awaiting response...")
        msg = json.loads(await self.hass_ws.recv(decode=True))

        if msg["type"] == "auth_ok":
            LOG.info("Home Assistant authentication successful")
        elif msg["type"] == "auth_invalid":
            raise RuntimeError("Home Assistant authentication failed, check token")
        else:
            raise RuntimeError("Unexpected response: %r", msg)

        LOG.debug("Enabling coalesced messages feature")
        await self.hass_ws.send(
            json.dumps(
                {
                    "id": 1,
                    "type": "supported_features",
                    "features": {"coalesce_messages": 1},
                }
            )
        )

        msg = json.loads(await self.hass_ws.recv(decode=True))
        LOG.debug("Response: %r", msg)
        assert msg["success"]

    async def subscribe_to_trigger(self, trigger: Any) -> int:
        """Subscribes to a trigger and returns the subscription ID"""

        key = stringify(trigger)

        if id := self.trigger_subscriptions.get(key):
            LOG.debug("Reusing existing trigger subscription: %r", id)
            return id

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

        self.trigger_subscriptions[key] = id

        return id

    async def perform_action(
        self,
        domain: str,
        name: str,
        /,
        target: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
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

    async def message(self, obj) -> Any:
        """Sends a message to Home Assistant and returns the response"""

        id = self.msg_id
        self.msg_id += 1

        msg = {**obj, "id": id}

        LOG.debug("Sending message to Home Assistant: %r", msg)

        await self.hass_ws.send(json.dumps(msg))

        while True:
            if id in self.msg_cache:
                msg = self.msg_cache.pop(id)
                assert len(msg) == 1

                return msg[0]

            await self.msg_event.wait()

    def load_automations(self):
        for package_name in self.config.packages:
            LOG.info("Importing package: %s", package_name)

            try:
                package = importlib.import_module(package_name)
            except ImportError:
                LOG.exception("Failed to import package: %s", package_name)
                continue

            package_path = Path(package.__path__[0])

            for path in package_path.glob("automations/*.py"):
                if path.name == "__init__.py":
                    continue

                module_name = path.stem

                self.automations[package_name] = Automation(
                    f"{package_name}.automations.{module_name}"
                )
                self.automations[package_name].load()

    async def init_automations(self):
        for automation in self.automations.values():
            if not automation.loaded:
                continue

            for trigger, handler in automation.trigger_handlers:
                id = await self.subscribe_to_trigger(trigger)

                self.event_handlers.setdefault(id, []).append((automation, handler))


class Automation:
    module_name: str
    module: ModuleType
    name: str
    loaded: bool
    trigger_handlers: list[tuple[dict, Callable]]

    def __init__(self, module_name: str):
        self.module_name = module_name
        self.name = module_name.split(".")[-1]
        self.loaded = False
        self.trigger_handlers = []

    def load(self):
        assert not self.loaded
        assert not neuron.api._trigger_handlers

        try:
            self.module = importlib.import_module(self.module_name)
        except Exception:
            LOG.exception("Failed to load module %r", self.module_name)

        self.trigger_handlers = neuron.api._trigger_handlers.copy()
        neuron.api._trigger_handlers.clear()

        self.name = getattr(self.module, "NAME", self.module_name)
        self.loaded = True

        LOG.info("Loaded automation: %s", self.module_name)
