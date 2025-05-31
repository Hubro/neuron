import asyncio
import logging
from pathlib import Path
from typing import AsyncGenerator, Callable, cast

import watchdog.events
import watchdog.observers

from .util import terse_module_path

LOG = logging.getLogger(__name__)


async def watch_automation_modules(
    packages: list[Path],
) -> AsyncGenerator[list[str], None]:
    loop = asyncio.get_running_loop()
    touched = set()
    reload_event = asyncio.Event()
    debounce_handle: asyncio.TimerHandle | None = None

    # Sets the reload event, but debounced with a slight delay to reduce noise
    def touch_paths(paths: set[str]):
        nonlocal touched, debounce_handle

        touched |= paths

        if debounce_handle:
            debounce_handle.cancel()

        debounce_handle = loop.call_later(0.25, reload_event.set)

    observer = watchdog.observers.Observer()
    observer.setName("neuron-automation-module-watcher")

    for path in packages:
        observer.schedule(
            SourceChangeHandler(loop, touch_paths),
            path=str(path / "automations"),
        )

    try:
        observer.start()

        while True:
            await reload_event.wait()
            reload_event.clear()

            LOG.debug(
                "Automation modules touched: %r",
                [terse_module_path(str(path)) for path in sorted(touched)],
            )
            yield sorted(touched)
            touched.clear()
    finally:
        observer.stop()
        observer.join(3)
        assert not observer.is_alive()


class SourceChangeHandler(watchdog.events.FileSystemEventHandler):
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        notify: Callable[[set[str]], None],
    ) -> None:
        self.loop = loop
        self.notify = notify

    def on_any_event(self, event: watchdog.events.FileSystemEvent) -> None:
        if event.is_directory:
            return

        # event.event_type is one of:
        # moved deleted created modified closed closed_no_write opened
        if event.event_type not in ("moved", "deleted", "created", "modified"):
            return

        src = cast(str, event.src_path)
        dst = cast(str, event.dest_path)

        touched = set()

        for path in [src, dst]:
            if not path.endswith(".py") or path.endswith("__init__.py"):
                continue

            if path in touched:
                continue

            touched.add(path)

        self.loop.call_soon_threadsafe(self.notify, touched)
