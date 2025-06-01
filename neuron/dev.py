from __future__ import annotations

import asyncio
import importlib
import logging
import os
import signal
import sys

import rich.logging

from neuron.logging import get_logger
from neuron.util import debounce

LOG = get_logger("neuron.dev")

_reload_event = asyncio.Event()


def sigusr1_handler():
    from neuron.util import log_tasks_and_threads

    log_tasks_and_threads()


async def main():
    level = logging.DEBUG if os.environ.get("VERBOSE") else logging.INFO
    level = 5 if os.environ.get("TRACE") else level

    logging._nameToLevel["TRACE"] = 5
    logging._levelToName[5] = "TRACE"

    logging.basicConfig(
        level=level,
        handlers=[rich.logging.RichHandler(rich_tracebacks=True, markup=True)],
        format=r"[blue b]\[%(name)s][/] %(message)s",
    )
    logging.getLogger("websockets.client").setLevel(logging.INFO)
    logging.getLogger("watchdog").setLevel(logging.INFO)

    asyncio.create_task(auto_reload_task(), name="neuron_core_auto_reload")

    loop = asyncio.get_running_loop()
    signal.signal(
        signal.SIGUSR1,
        lambda *args: loop.call_soon_threadsafe(sigusr1_handler),
    )

    try:
        while True:
            core = importlib.import_module("neuron.core")

            neuron = core.Neuron()
            neuron_task = asyncio.create_task(neuron.start(), name="neuron")

            await asyncio.wait(
                [
                    asyncio.create_task(_reload_event.wait()),
                    neuron_task,
                ],  # type: ignore
                return_when=asyncio.FIRST_COMPLETED,
            )
            _reload_event.clear()

            neuron_task.cancel()
            try:
                await neuron_task
            except Exception as e:
                LOG.fatal("Neuron crashed", exc_info=e)
                await _reload_event.wait()
                _reload_event.clear()
            except asyncio.CancelledError:
                pass  # Ignore CancelledError coming from the Neuron task

            while True:
                try:
                    reload_neuron()
                    break
                except Exception:
                    LOG.exception("Failed to reload neuron")
                    await _reload_event.wait()
                    _reload_event.clear()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


@debounce(seconds=0.25)
async def signal_neuron_reload():
    """Signals the main loop to reload Neuron"""
    _reload_event.set()


async def auto_reload_task():
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    e = asyncio.Event()
    loop = asyncio.get_running_loop()

    class SourceChangeHandler(FileSystemEventHandler):
        def on_any_event(self, event: FileSystemEvent) -> None:
            assert isinstance(event.src_path, str)
            assert isinstance(event.dest_path, str)

            if event.is_directory:
                return

            main = "neuron/main.py"
            if event.src_path == main or event.dest_path == main:
                return

            if event.event_type in ("opened", "closed_no_write"):
                return

            if any(path.endswith(".py") for path in [event.src_path, event.dest_path]):
                LOG.info(
                    "File changed: event_type=%r src_path=%r dest_path=%r",
                    event.event_type,
                    event.src_path,
                    event.dest_path,
                )
                loop.call_soon_threadsafe(e.set)

    observer = Observer()
    observer.schedule(SourceChangeHandler(), path="neuron", recursive=True)
    observer.start()

    try:
        while True:
            await e.wait()
            e.clear()
            await signal_neuron_reload()
    except asyncio.CancelledError:
        pass


def reload_neuron():
    """Reloads all of Neuron's Python modules"""

    neuron_modules = [
        module
        for name, module in sys.modules.items()
        if name.startswith("neuron") and name != __name__
    ]

    for module in neuron_modules:
        LOG.info("Reloading module: %s", module.__name__)
        importlib.reload(module)


if __name__ == "__main__":
    asyncio.run(main())
