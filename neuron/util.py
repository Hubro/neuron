import asyncio
import sys
import threading
from contextlib import asynccontextmanager
from functools import wraps
from types import FrameType
from typing import Any, Awaitable, Callable

import orjson

from .config import load_config
from .logging import get_logger

LOG = get_logger(__name__)


def stringify(obj: Any) -> str:
    return orjson.dumps(obj, option=orjson.OPT_SORT_KEYS).decode()


def has_keyword_arg(fn: Callable, name: str):
    return name in fn.__code__.co_varnames


def filter_keyword_args(fn: Callable, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Returns a dict with only the keyword arguments that fn accepts"""
    return {key: value for key, value in kwargs.items() if has_keyword_arg(fn, key)}


def first_relevant_frame(frame: FrameType) -> FrameType:
    relevant_frame = None

    while True:
        is_neuron = "/neuron/" in frame.f_code.co_filename
        is_site_package = "/site-packages/" in frame.f_code.co_filename
        is_module = frame.f_code.co_name == "<module>"

        if not is_module and (is_neuron or is_site_package):
            relevant_frame = frame

        if frame.f_back is None:
            break

        frame = frame.f_back

    if relevant_frame:
        return relevant_frame

    return frame


def terse_module_path(path: str) -> str:
    config = load_config()

    for package in config.packages:
        if len(split := path.split(f"{package}/")) > 1:
            return f"{package}/{split[-1]}"

    if len(split := path.split("neuron/")) > 1:
        return f"neuron/{split[-1]}"

    if len(split := path.split("site-packages/")) > 1:
        return split[-1]

    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    if len(split := path.split(f"/lib/python{version}/")) > 1:
        return split[-1]

    return path


def log_tasks_and_threads():
    threads = sorted(threading.enumerate(), key=lambda x: x.name)
    tasks = sorted(asyncio.all_tasks(), key=lambda x: x.get_name())

    frames = sys._current_frames()

    def fmt_thread(thread: threading.Thread) -> str:
        name = f"[yellow]{thread.name}[/]"
        status = "[green]alive[/]" if thread.is_alive() else "[red]dead[/]"

        assert thread.ident is not None
        frame = first_relevant_frame(frames[thread.ident])
        file = terse_module_path(frame.f_code.co_filename)
        ln = frame.f_code.co_firstlineno
        func = frame.f_code.co_qualname
        details = f"running [blue bold]{func}[/] at [green]{file}:{ln}[/]"
        return f"{name} ({status}) {details}"

    def fmt_task(task: asyncio.Task) -> str:
        name = f"[yellow]{task.get_name()}[/]"
        status = "[red]done[/]" if task.done() else "[green]running[/]"

        frame = task.get_stack()[-1]
        file = terse_module_path(frame.f_code.co_filename)
        ln = frame.f_code.co_firstlineno
        func = frame.f_code.co_qualname
        details = f"running [bold blue]{func}[/] at [green]{file}:{ln}[/]"

        return f"{name} ({status}) {details}"

    lines = ["Dumping all current tasks and threads"]
    lines += [f"Threads ({len(threads)}):"]
    lines += [f"  - {fmt_thread(thread)}" for thread in threads]
    lines += [f"Tasks ({len(tasks)}):"]
    lines += [f"  - {fmt_task(task)}" for task in tasks]

    LOG.info("\n".join(lines), extra={"highlighter": False})


def debounce(seconds: float):
    handle: asyncio.Handle | None = None

    def decorator(fn: Callable):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            nonlocal handle

            if handle:
                handle.cancel()

            handle = asyncio.get_running_loop().call_later(
                seconds, lambda: fn(*args, **kwargs)
            )

        return wrapped

    return decorator


# TODO: Rewrite this to create and return a task if I ever need it for anything
#
# async def delay(seconds: float, fn: Callable[..., Awaitable[None]], /, *args, **kwargs):
#     """Use with asyncio.create_task to execute a function after a delay"""
#     try:
#         LOG.trace("(delay) Calling function %r in %r seconds", fn.__name__, seconds)
#         await asyncio.sleep(seconds)

#         # Run the function in a new task so it won't be cancelled together with
#         # the delay task
#         LOG.trace("(delay) Calling function %r now!", fn.__name__)
#         asyncio.create_task(fn(*args, **kwargs), name=f"delay-{fn.__name__}")  # type: ignore
#     except asyncio.CancelledError:
#         LOG.trace("(delay) Delay for %r cancelled", fn.__name__)
#         pass


@asynccontextmanager
async def wait_event(*events: asyncio.Event):
    """Yields the first async event to be set

    Usage:

        async with wait_event(new_message, reconnected) as event:
            if event is new_message:
                continue

            if event is reconnected:
                ...
    """

    map = {asyncio.create_task(event.wait()): event for event in events}

    done, pending = await asyncio.wait(map.keys(), return_when=asyncio.FIRST_COMPLETED)
    assert len(done) == 1

    for task in pending:
        task.cancel()

    yield map[done.pop()]
