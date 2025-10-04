# pyright: enableExperimentalFeatures=true
from __future__ import annotations

import asyncio
import importlib
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterator, overload

from typing_extensions import Sentinel

import neuron.api
import neuron.bus

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


# Used in place of Automation in some places to represent Neutron core
NEURON_CORE = Sentinel("NEURON_CORE")


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
                    LOG.exception(
                        "Unhandled exception in task %r", task.get_name(), exc_info=e
                    )
                    raise e
                elif task.get_name() == "neuron_wait-for-stop-signal":
                    pass
                else:
                    LOG.fatal(
                        f"Background task {task.get_name()} has exited unexpectedly!"
                    )

            LOG.warning("Shutting down because one or more background tasks failed")
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
                        LOG.info("Reconnected to Home Assistant")
                        await self.reestablish_subscriptions()

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
            handler_kwargs["event_type"] = event_msg["event"]["event_type"]
            handler_kwargs["event"] = event_msg["event"]["data"]

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

        # The subscriptions might mutate as a result of dispatching this event,
        # so we need to copy the list of handlers before iterating over it
        handlers = self.subscriptions[id].handlers.copy()

        for automation, handler in handlers:
            # In case the handler was unsubscribed in a previous iteration
            if (automation, handler) not in self.subscriptions[id].handlers:
                continue

            logger = LOG if automation is NEURON_CORE else automation.logger

            handler_kwargs["log"] = logger

            handler_name = handler.__name__
            handler_is_event_wrapper = getattr(handler, "_event_handler_wrapper", False)
            handler_is_internal = (
                getattr(handler, "__func__", None)
                is Automation.subscribe_entities_handler
            ) or handler_is_event_wrapper

            try:
                kwargs = filter_keyword_args(handler, handler_kwargs)

                # If this is a handler wrapper made by "on_event", pass the
                # full kwargs dict so it can be used for filtering
                if handler_is_event_wrapper:
                    kwargs["handler_kwargs"] = handler_kwargs

                if not handler_is_internal:
                    logger.debug("Executing handler: %s", handler_name)
                    logger.trace("Handler arguments: %r", handler_kwargs)

                if automation is NEURON_CORE:
                    await handler(**kwargs)
                else:
                    with automation.api_context():
                        await handler(**kwargs)
            except Exception:
                logger.exception(
                    "Failed to execute subscription handler %r", handler_name
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

                try:
                    await self.load_automation(module_path)
                except Exception:
                    LOG.exception("Failed to load automation: %s", module_path.name)

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
                await self.load_automation(module_path, reload=True)
            else:
                # Module was added
                LOG.info(
                    "Adding new automation: %s",
                    terse_module_path(str(module_path)),
                )
                await self.load_automation(module_path)

    async def load_automation(self, module_path: Path, reload=False):
        """Loads an automation from a module path"""

        automation = Automation(module_path)
        LOG.info("Loading automation: %s", automation.module_name)

        assert automation.module_name not in self.automations
        self.automations[automation.module_name] = automation

        automation.load(reload=reload)

        # No point in proceeding if loading the module failed. An error has
        # already been logged.
        if not automation.module:
            return

        LOG.debug("Establishing subscriptions")
        establish_subscriptions = self.establish_subscriptions(automation)
        await asyncio.wait_for(establish_subscriptions, timeout=10.0)

        LOG.debug("Awaiting initial entity states before proceeding")
        for entity in automation.entities.values():
            try:
                async with asyncio.timeout(3.0):
                    await entity.initialized.wait()
            except asyncio.TimeoutError:
                LOG.error(
                    f"Timed out waiting for the initial state of {entity.entity_id!r}, perhaps it doesn't exist?"
                )
                await self.eject_automation(automation)
                return

        if hasattr(automation.module, "init"):
            LOG.debug("Executing the automation's init function")
            kwargs = filter_keyword_args(
                automation.module.init, {"log": automation.logger}
            )

            with automation.api_context():
                result = automation.module.init(**kwargs)

                if asyncio.iscoroutine(result):
                    LOG.debug("Awaiting coroutine returned by init function")
                    await result

        LOG.info("Automation loaded and initialized successfully")

    async def establish_subscriptions(self, automation: Automation):
        if automation in self.subscriptions:
            LOG.error(
                "Subscriptions already established for automation %r",
                automation.module_name,
            )
            return

        for trigger, handler in automation.trigger_handlers:
            await self.subscribe(automation, handler, to=trigger)

        for event, handler in automation.event_handlers:
            await self.subscribe(automation, handler, to=event)

        if automation.entities:
            await self.subscribe(
                automation,
                automation.subscribe_entities_handler,
                to=list(automation.entities.values()),
            )

    async def reestablish_subscriptions(self):
        """Re-establishes all subscriptions after reconnecting to Home Assistant

        Note: This function assumes that all current subscriptions are already
        dead and the HA connection is fresh. Old subscriptions are dropped with
        no cleanup.
        """

        LOG.info("Re-establishing subscriptions")

        old_subscriptions = self.subscriptions
        self.subscriptions = Subscriptions()

        for old_subscription in old_subscriptions:
            for handler in old_subscription[NEURON_CORE]:
                await self.subscribe(NEURON_CORE, handler, to=old_subscription.key)

            for automation in old_subscription.automations:
                for handler in old_subscription[automation]:
                    await self.subscribe(automation, handler, to=old_subscription.key)

    async def subscribe(
        self,
        automation: Automation | NEURON_CORE,
        handler: Callable,
        *,
        to: str | dict[str, Any] | list[Entity],
    ):
        subscription = self.subscriptions.get(to)

        if not subscription:
            if isinstance(to, str):
                event = to
                id = await self.hass.subscribe_to_events(event)
                subscription = Subscription(id, event=event)
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

    async def unsubscribe(self, handler: Callable, event_or_trigger: str | dict):
        """Unsubscribes a handler from an event or trigger"""

        subscription = self.subscriptions[event_or_trigger]
        del subscription[handler]

        await self.prune_subscriptions()

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

    def _log_state(self):
        """Logs the entire application state for debugging"""
        dump = []

        dump.append(f"neuron.config = {self.config!r}")

        dump.append("neuron.automations =")
        for automation in self.automations.values():
            dump.append(f"  - {automation!r}")

        dump.append(f"neuron.packages = {self.packages!r}")

        dump.append("neuron.tasks =")
        for task in self.tasks:
            dump.append(f"  - {task.get_name()}")

        dump.append("neuron.subscriptions =")
        for subscription in self.subscriptions:
            dump.append(f"  {subscription.id}: {subscription!r}")

        LOG.debug("Neuron state:\n%s", "\n".join(dump))


class Subscriptions:
    """Data structure for managing subscriptions"""

    def __init__(self):
        # Maps HASS subscription ID to Subscription
        self._subscriptions: dict[int, Subscription] = {}

        # Maps event/trigger/entities to subscription. Since events are
        # strings, triggers are objects and entity lists are lists, there is no
        # overlap between subscription types.
        self._reverse_map: dict[str, Subscription] = {}

        self._automation_map: dict[Automation | NEURON_CORE, set[Subscription]] = {}

    def __iter__(self) -> Iterator[Subscription]:
        """Iterate over all subscriptions

        The subscriptions are yielded in the order they should be processed.
        Entity state subscriptions first, then events, then triggers. This
        order ensures that all entities have the right state when trigger/event
        handlers are executed.
        """

        def sort_key(sub: Subscription):
            if sub.entities:
                return 1
            elif sub.event:
                return 2
            else:
                return 3

        for subscription in sorted(self._subscriptions.values(), key=sort_key):
            yield subscription

    @overload
    def __getitem__(
        self, key: int | str | dict[str, Any] | list[Entity]
    ) -> Subscription:
        """Returns a subscription by its ID or event/trigger"""
        ...

    @overload
    def __getitem__[T](self, key: NEURON_CORE) -> set[Subscription]:
        """Returns all Neutron internal subscriptions"""
        ...

    @overload
    def __getitem__(self, key: Automation) -> set[Subscription]:
        """Returns all subscriptions for an automation"""
        ...

    def __getitem__(
        self, key: int | str | dict[str, Any] | Automation | NEURON_CORE | list[Entity]
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
    def get[T](self, key: NEURON_CORE, default: T = None) -> set[Subscription] | T:
        """Returns all Neutron internal subscriptions"""
        ...

    @overload
    def get[T](self, key: Automation, default: T = None) -> set[Subscription] | T:
        """Returns all subscriptions for an automation"""
        ...

    def get[T](
        self,
        key: int | str | dict[str, Any] | list[Entity] | Automation | NEURON_CORE,
        default: T = None,
    ) -> Subscription | set[Subscription] | T:
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(
        self, key: int | str | list[Entity] | dict[str, Any] | Automation | NEURON_CORE
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

    _handlers: dict[Automation | NEURON_CORE, list[Callable]] = field(
        default_factory=dict
    )

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

    @overload
    def __getitem__(self, automation: Automation) -> list[Callable]:
        """Returns all the handlers for this subscription from the given automation"""
        ...

    @overload
    def __getitem__(self, automation: NEURON_CORE) -> list[Callable]:
        """Returns all Neuron internal handlers for this subscription"""
        ...

    def __getitem__(self, automation: Automation | NEURON_CORE) -> list[Callable]:
        return self._handlers[automation]

    @overload
    def __contains__(self, automation: Automation) -> bool:
        """Returns True if the subscription has any internal Neuron handlers"""
        return automation in self._handlers

    @overload
    def __contains__(self, automation: NEURON_CORE) -> bool:
        """Returns True if the subscription has any handlers from the given automation"""
        return automation in self._handlers

    def __contains__(self, automation: Automation | NEURON_CORE) -> bool:
        return automation in self._handlers

    @overload
    def __delitem__(self, x: Automation | NEURON_CORE):
        """Deletes the handlers from the given automation"""
        ...

    @overload
    def __delitem__(self, x: Callable):
        """Deletes the given handler from this subscription"""
        ...

    def __delitem__(self, x: Automation | NEURON_CORE | Callable):
        if isinstance(x, Automation) or x is NEURON_CORE:
            del self._handlers[x]
        else:
            for automation, handlers in list(self._handlers.items()):
                if x in handlers:
                    handlers.remove(x)

                    if not handlers:
                        del self[automation]

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
        return [x for x in self._handlers.keys() if isinstance(x, Automation)]

    @property
    def handlers(self) -> list[tuple[Automation | NEURON_CORE, Callable]]:
        """Returns all handlers for this subscription"""

        result = []

        for automation, handlers in self._handlers.items():
            for handler in handlers:
                result.append((automation, handler))

        return result

    def add_handler(self, automation: Automation | NEURON_CORE, handler: Callable):
        handlers = self._handlers.setdefault(automation, [])

        source = "core" if automation is NEURON_CORE else automation.module_name

        if handler in handlers:
            LOG.error(
                "Refusing to add handler %r (from %s) to subscription %r more than once",
                handler.__name__,
                source,
                self.id,
            )
            return

        handlers.append(handler)


class Automation:
    module_name: str
    module: ModuleType | None
    module_path: Path
    loaded: bool
    logger: NeuronLogger
    trigger_handlers: list[tuple[dict, Callable]]
    event_handlers: list[tuple[str, Callable]]
    entities: dict[str, Entity]

    def __init__(self, module_path: Path):
        package_path = module_path.parent.parent
        self.module_name = f"{package_path.name}.automations.{module_path.stem}"
        self.module = None
        self.module_path = module_path
        self.loaded = False
        self.logger = get_logger(self.module_name)
        self.trigger_handlers = []
        self.event_handlers = []
        self.entities = {}

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.module_name!r}>"

    def __hash__(self) -> int:
        return hash(self.module_name)

    def load(self, reload=False):
        assert not self.loaded
        assert not neuron.api._trigger_handlers

        try:
            self.module = importlib.import_module(self.module_name)
        except Exception:
            LOG.exception("Failed to load module %r", self.module_name)
            return

        if reload:
            # Make doubly sure the module is actually reloaded and not just
            # quick-loaded from cache
            self.module = importlib.reload(self.module)

        assert isinstance(self.module.__file__, str)
        self.module_path = Path(self.module.__file__).resolve()
        assert self.module_path.is_file()

        self.trigger_handlers = neuron.api._trigger_handlers.copy()
        self.event_handlers = neuron.api._event_handlers.copy()
        self.entities = neuron.api._entities.copy()
        neuron.api._clear()

        self.name = getattr(self.module, "NAME", self.module_name)
        self.loaded = True

    @contextmanager
    def api_context(self):
        """Readies the API context for executing handlers from this automation"""

        restore = neuron.api._logger.get()
        neuron.api._logger.set(self.logger)

        neuron.api._automation.set(self)

        try:
            yield
        finally:
            neuron.api._logger.set(restore)

    async def subscribe_entities_handler(self, entity_states: dict[str, Any]):
        for entity_id, state_object in entity_states.items():
            if entity_id not in self.entities:
                LOG.error(f"Got unexpected entity state update for {entity_id!r}")
                continue

            entity = self.entities[entity_id]

            if "+" in state_object:
                diff = state_object["+"]

                if "s" in diff:
                    entity._state = diff["s"]

                if "a" in diff:
                    entity._attributes.update(diff["a"])

                LOG.debug("Updated entity state: %r", entity)
            else:
                entity._state = state_object["s"]
                entity._attributes = state_object["a"]
                entity.initialized.set()
                LOG.debug("Set initial entity state: %r", entity)
