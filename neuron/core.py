from __future__ import annotations

import asyncio
import importlib
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterator, cast, overload

import neuron.api

from .api import Entity, StateChange
from .config import Config, load_config
from .hass import HASS
from .logging import NeuronLogger, get_logger
from .util import (
    filter_keyword_args,
    stringify,
    terse_module_path,
    wait_event,
)
from .watch import watch_automation_modules

LOG = get_logger(__name__)


class Neuron:
    config: Config
    hass: HASS
    automations: dict[str, Automation]
    packages: list[Path]  # Packages we've loaded automations from
    tasks: list[asyncio.Task]
    subscriptions: Subscriptions

    _stop: asyncio.Event

    def __init__(self) -> None:
        self.automations = {}
        self.packages = []
        self.config = load_config()
        self.hass = HASS(self.config.hass_websocket_uri, self.config.hass_api_token)
        self.tasks = []
        self.subscriptions = Subscriptions()
        self._stop = asyncio.Event()

    async def start(self):
        LOG.info("Starting Neuron!")

        await self.load_packages()
        await self.hass.connect()

        start_task = lambda task, name=None: self.tasks.append(
            asyncio.create_task(task(), name=(name or f"neuron-{task.__name__}"))
        )
        start_task(self.hass.message_handler_task)
        start_task(self.event_subscription_handler_task)
        start_task(self.auto_reload_automations_task)
        start_task(self._stop.wait, name="neuron_wait-for-stop-signal")

        neuron.api._reset()
        neuron.api._neuron = self  # Any API usage will now target this Neuron instance

        # Load automations in a separate task so we can proceed with monitoring
        # the core tasks
        asyncio.create_task(self.load_automations(), name="neuron-load_automations")

        try:
            done, pending = await asyncio.wait(
                self.tasks, return_when=asyncio.FIRST_COMPLETED
            )

            for task in done:
                if e := task.exception():
                    raise e
                elif task.get_name() == "neuron_wait-for-stop-signal":
                    pass
                else:
                    raise RuntimeError(
                        f"Background task {task.get_name()} has exited unexpectedly!"
                    )

            return
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            LOG.info("Shutting down gracefully")

            try:
                await asyncio.wait_for(self._shutdown(), timeout=2)
                LOG.info("Bye!")
            except asyncio.TimeoutError:
                LOG.error("Failed to shut down gracefully, timeout reached")

    def stop(self):
        """Signals Neuron to shut down gracefully"""
        self._stop.set()

    async def _shutdown(self):
        LOG.info("Ejecting all automation modules")
        for automation in list(self.automations.values()):
            await self.eject_automation(automation)

        LOG.info("Unsubscribing from events")
        await self.prune_subscriptions()

        LOG.info("Shutting down background tasks")
        for task in self.tasks:
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass

        LOG.info("Closing Home Assistant Websocket connection")
        await self.hass.disconnect()

    async def event_subscription_handler_task(self):
        """Async task for keeping track of subscriptions and calling handlers"""

        new_message = self.hass.messages.on_new_message.event()
        reconnected = self.hass.on_reconnect.event()

        try:
            while True:
                for subscription in self.subscriptions:
                    for msg in self.hass.messages.pop(subscription.id, []):
                        await self.dispatch_event(msg)

                async with wait_event(new_message, reconnected) as event:
                    if event is new_message:
                        pass

                    if event is reconnected:
                        LOG.info(
                            "Reconnected to Home Assistant, restarting subscriptions"
                        )
                        self.subscriptions = Subscriptions()

                        for automation in self.automations.values():
                            await self.establish_subscriptions(automation)

        except asyncio.CancelledError:
            return

    async def dispatch_event(self, event_msg: dict[str, Any]):
        assert event_msg["type"] == "event"

        # Note: The ID of a subscription can not be relied upon, since it will
        # change if/when we have to reconnect to Home Assistant and
        # re-establish all the subscriptions. Deleting it here makes sure
        # automations don't accidentally use it for something.
        id: int = event_msg.pop("id")

        if id not in self.subscriptions:
            LOG.warning("Got event message with no subscribers: %r", event_msg)
            return

        is_event = "data" in event_msg["event"]
        is_trigger = "variables" in event_msg["event"]
        is_entities = "a" in event_msg["event"] or "c" in event_msg["event"]
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

        elif is_entities:
            a = event_msg["event"].get("a")
            c = event_msg["event"].get("c")

            handler_kwargs["entity_states"] = a or c

        else:
            raise RuntimeError("Unrecognized event message: %r", event_msg)

        for automation, handler in self.subscriptions[id].handlers:
            try:
                kwargs = filter_keyword_args(handler, handler_kwargs)

                with automation.override_api_logger():
                    await handler(**kwargs)
            except Exception:
                logger = get_logger(handler.__module__)
                logger.exception(
                    "Failed to execute subscription handler %r", handler.__name__
                )

    async def auto_reload_automations_task(self):
        try:
            LOG.info("Watching automation modules")

            async for touched_modules in watch_automation_modules(self.packages):
                await self.reload_automations(touched_modules)
        except asyncio.CancelledError:
            return

    async def load_packages(self):
        for package_name in self.config.packages:
            LOG.info("Importing package: %s", package_name)

            try:
                package = importlib.import_module(package_name)
            except ImportError:
                LOG.exception("Failed to import package: %s", package_name)
                continue

            package_path = Path(package.__path__[0]).resolve()
            self.packages.append(package_path)

    async def load_automations(self):
        # TODO: Load automations concurrently in separate tasks. Establishing
        # subscriptions is an async operation so it's efficient to do it
        # concurrently.
        for package_path in self.packages:
            for module_path in package_path.glob("automations/*.py"):
                if module_path.name == "__init__.py":
                    continue

                await self.load_automation(module_path)

    async def reload_automations(self, module_paths: list[str]):
        """Reloads the given automation module paths"""

        automation_module_path_map = {
            automation.module_path: automation
            for automation in self.automations.values()
        }

        for module_path_str in module_paths:
            module_path = Path(module_path_str)

            if not module_path.exists():
                # Module was deleted
                if automation := automation_module_path_map.get(module_path):
                    LOG.info("Unloading deleted automation: %s", automation.module_name)
                    await self.eject_automation(automation)
                else:
                    LOG.warning(
                        "Deleted automation module was never loaded: %s", module_path
                    )
            elif automation := automation_module_path_map.get(module_path):
                # Module was modified
                LOG.info("Reloading modified automation: %s", automation.module_name)
                await self.eject_automation(automation)
                await self.load_automation(module_path)
            else:
                # Module was added
                LOG.info(
                    "Adding new automation: %s",
                    terse_module_path(str(module_path)),
                )
                await self.load_automation(module_path)

    async def load_automation(self, module_path: Path):
        """Loads an automation from a module path"""

        automation = Automation(module_path)
        LOG.info("Loading automation: %s", automation.module_name)

        assert automation.module_name not in self.automations
        self.automations[automation.module_name] = automation

        automation.load()

        await self.establish_subscriptions(automation)

        # Wait for entities to saturate before calling init
        for entity in automation.entities.values():
            await entity.initialized.wait()

        if hasattr(automation.module, "init"):
            result = automation.module.init()

            if asyncio.iscoroutine(result):
                await result

    async def establish_subscriptions(self, automation: Automation):
        if automation in self.subscriptions:
            LOG.error(
                "Subscriptions already established for automation %r",
                automation.module_name,
            )
            return

        for trigger, handler in automation.trigger_handlers:
            await self.subscribe(automation, handler, to=trigger)

        if automation.entities:
            await self.subscribe(
                automation,
                automation.subscribe_entities_handler,
                to=list(automation.entities.values()),
            )

    async def subscribe(
        self,
        automation: Automation,
        handler: Callable,
        *,
        to: str | dict[str, Any] | list[Entity],
    ):
        if isinstance(to, str):
            raise NotImplementedError()

        subscription = self.subscriptions.get(to)

        if not subscription:
            if isinstance(to, str):
                raise NotImplementedError()
            elif isinstance(to, dict):
                trigger = to
                id = await self.hass.subscribe_to_trigger(trigger)
                subscription = Subscription(id, trigger=trigger)
            else:
                entities = to
                id = await self.hass.subscribe_to_entities(entities)
                subscription = Subscription(id, entities=entities)

        subscription.add_handler(automation, handler)
        self.subscriptions.add(subscription)

    async def eject_automation(self, automation: Automation):
        LOG.info("Ejecting automation: %s", automation.module_name)

        subscriptions = self.subscriptions.get(automation, [])

        for subscription in subscriptions:
            if automation in subscription:
                del subscription[automation]

        await self.prune_subscriptions()

        del self.automations[automation.module_name]

        # Module might have failed to load
        if automation.module_name in sys.modules:
            sys.modules.pop(automation.module_name)

    async def prune_subscriptions(self):
        """Unsubscribes from all events for which there are no remaining handlers"""

        for subscription in list(self.subscriptions):
            if not subscription.handlers:
                LOG.debug(
                    "Subscription %r no longer has any handlers, unsubscribing",
                    subscription.id,
                )
                await self.hass.unsubscribe(subscription.id)
                del self.subscriptions[subscription]


class Subscriptions:
    """Data structure for managing subscriptions"""

    def __init__(self):
        # Maps HASS subscription ID to Subscription
        self._subscriptions: dict[int, Subscription] = {}

        # Maps event/trigger/entities to subscription. Since events are
        # strings, triggers are objects and entity lists are lists, there is no
        # overlap between subscription types.
        self._reverse_map: dict[str, Subscription] = {}

        self._automation_map: dict[Automation, set[Subscription]] = {}

    def __iter__(self) -> Iterator[Subscription]:
        """Iterate over all subscriptions"""

        for subscription in self._subscriptions.values():
            yield subscription

    @overload
    def __getitem__(
        self, key: int | str | dict[str, Any] | list[Entity]
    ) -> Subscription:
        """Returns a subscription by its ID or event/trigger"""
        ...

    @overload
    def __getitem__(self, key: Automation) -> set[Subscription]:
        """Returns all subscriptions for an automation"""
        ...

    def __getitem__(
        self, key: int | str | dict[str, Any] | Automation | list[Entity]
    ) -> Subscription | set[Subscription]:
        if isinstance(key, int):
            return self._subscriptions[key]
        elif isinstance(key, str):
            return self._reverse_map[key]
        elif isinstance(key, dict):
            return self._reverse_map[stringify(key)]
        elif isinstance(key, list):
            return self._reverse_map[stringify([x.entity_id for x in key])]
        else:
            return self._automation_map[key].copy()

    @overload
    def get[T](
        self, key: int | str | dict[str, Any] | list[Entity], default: T = None
    ) -> Subscription | T:
        """Returns a subscription by its ID or event/trigger"""
        ...

    @overload
    def get[T](self, key: Automation, default: T = None) -> set[Subscription] | T:
        """Returns all subscriptions for an automation"""
        ...

    def get[T](
        self,
        key: int | str | dict[str, Any] | list[Entity] | Automation,
        default: T = None,
    ) -> Subscription | set[Subscription] | T:
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(
        self, key: int | str | list[Entity] | dict[str, Any] | Automation
    ) -> bool:
        if isinstance(key, int):
            return key in self._subscriptions
        elif isinstance(key, str):
            return key in self._reverse_map
        elif isinstance(key, dict):
            return stringify(key) in self._reverse_map
        elif isinstance(key, list):
            return stringify([x.entity_id for x in key]) in self._reverse_map
        else:
            return key in self._automation_map

    def __delitem__(self, key: int | Subscription | Automation):
        """Deletes the given subscription or all subscriptions of an automation"""

        if isinstance(key, Automation):
            automation = key

            for subscription in self._automation_map[automation]:
                del subscription[automation]

        else:
            subscription = key

            if isinstance(subscription, int):
                subscription = self._subscriptions[subscription]

            del self._subscriptions[subscription.id]
            del self._reverse_map[subscription.key]

    def add(self, subscription: Subscription):
        self._subscriptions[subscription.id] = subscription
        self._reverse_map[subscription.key] = subscription

        for automation in subscription.automations:
            self._automation_map.setdefault(automation, set()).add(subscription)


@dataclass(frozen=True)
class Subscription:
    """Represents a single active HASS event subscription, can have many handlers"""

    id: int  # Subscription ID from HASS
    event: str | None = None  # Provide if event subscription
    trigger: dict | None = None  # Provide if trigger subscription
    entities: list[Entity] | None = None  # Provide if entities subscription

    _handlers: dict[Automation, list[Callable]] = field(default_factory=dict)

    def __post_init__(self):
        if (
            len([x for x in [self.event, self.trigger, self.entities] if x is not None])
            != 1
        ):
            raise ValueError("Must set either 'event', 'trigger' or 'entities'")

    def __hash__(self) -> int:
        return hash(self.id)

    def __iter__(self) -> Iterator[Callable]:
        """Iterate over all handlers"""

        for handlers in self._handlers.values():
            for handler in handlers:
                yield handler

    def __getitem__(self, automation: Automation) -> list[Callable]:
        """Returns all the handlers for this subscription from the given automation"""
        return self._handlers[automation]

    def __contains__(self, automation: Automation) -> bool:
        """Returns True if the subscription has any handlers from the given automation"""
        return automation in self._handlers

    def __delitem__(self, automation: Automation):
        """Deletes the handlers from the given automation"""
        del self._handlers[automation]

    @property
    def key(self) -> str:
        if self.event:
            return self.event
        elif self.trigger:
            return stringify(self.trigger)
        elif self.entities:
            return stringify([x.entity_id for x in self.entities])
        else:
            raise RuntimeError()

    @property
    def automations(self) -> list[Automation]:
        """Returns all automations with handlers for this subscription"""
        return list(self._handlers.keys())

    @property
    def handlers(self) -> list[tuple[Automation, Callable]]:
        """Returns all handlers for this subscription"""

        result = []

        for automation, handlers in self._handlers.items():
            for handler in handlers:
                result.append((automation, handler))

        return result

    def add_handler(self, automation: Automation, handler: Callable):
        handlers = self._handlers.setdefault(automation, [])

        if handler in handlers:
            LOG.error(
                "Refusing to add handler %r (from %s) to subscription %r more than once",
                handler.__name__,
                automation.module_name,
                self.id,
            )
            return

        handlers.append(handler)


class Automation:
    module_name: str
    module: ModuleType
    module_path: Path
    loaded: bool
    logger: NeuronLogger
    trigger_handlers: list[tuple[dict, Callable]]
    entities: dict[str, Entity]

    def __init__(self, module_path: Path):
        package_path = module_path.parent.parent
        self.module_name = f"{package_path.name}.automations.{module_path.stem}"
        self.module_path = module_path
        self.loaded = False
        self.logger = get_logger(self.module_name)
        self.trigger_handlers = []
        self.entities = {}

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.module_name!r}>"

    def __hash__(self) -> int:
        return hash(self.module_name)

    def load(self):
        assert not self.loaded
        assert not neuron.api._trigger_handlers

        try:
            self.module = importlib.import_module(self.module_name)
        except Exception:
            LOG.exception("Failed to load module %r", self.module_name)
            return

        assert isinstance(self.module.__file__, str)
        self.module_path = Path(self.module.__file__).resolve()
        assert self.module_path.is_file()

        self.trigger_handlers = neuron.api._trigger_handlers.copy()
        self.entities = neuron.api._entities.copy()
        neuron.api._trigger_handlers.clear()
        neuron.api._entities.clear()

        self.name = getattr(self.module, "NAME", self.module_name)
        self.loaded = True

    @contextmanager
    def override_api_logger(self):
        """Overrides the API logger for the duration of the context"""

        restore = neuron.api.LOG
        neuron.api.LOG = self.logger

        try:
            yield
        finally:
            neuron.api.LOG = restore

    async def subscribe_entities_handler(self, entity_states: dict[str, Any]):
        for entity_id, state_object in entity_states.items():
            if entity_id not in self.entities:
                LOG.error(f"Got unexpected entity state update for {entity_id!r}")
                continue

            entity = self.entities[entity_id]

            if "+" in state_object:
                diff = state_object["+"]
                entity._state = diff["s"]

                if "a" in diff:
                    entity._attributes.update(diff["a"])

                LOG.debug("Updated entity state: %r", entity)
            else:
                entity._state = state_object["s"]
                entity._attributes = state_object["a"]
                entity.initialized.set()
                LOG.debug("Set initial entity state: %r", entity)
