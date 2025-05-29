import os
import asyncio
import logging
from functools import wraps
from typing import Any, Awaitable, Callable

import orjson

LOG = logging.getLogger(__name__)

if "TRACE" in os.environ:
    trace = lambda *args: LOG.log(5, *args)  # noqa: E731
else:
    trace = lambda *args: None  # noqa: E731


def stringify(obj: Any) -> str:
    return orjson.dumps(obj, option=orjson.OPT_SORT_KEYS).decode()


def debounce(seconds: float):
    task: asyncio.Task | None = None

    def decorator(fn: Callable[..., Awaitable[None]]):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            nonlocal task

            if task and not task.done():
                trace("(debounce) Cancelling task %r", task.get_name())
                task.cancel()

            task = asyncio.create_task(
                delay(seconds, fn, *args, **kwargs),
                name=f"debounce-{fn.__name__}",
            )
            trace("(debounce) Created task %r", task.get_name())

        return wrapped

    return decorator


async def delay(seconds: float, fn: Callable[..., Awaitable[None]], /, *args, **kwargs):
    """Use with asyncio.create_task to execute a function after a delay"""
    try:
        trace("(delay) Calling function %r in %r seconds", fn.__name__, seconds)
        await asyncio.sleep(seconds)

        # Run the function in a new task so it won't be cancelled together with
        # the delay task
        trace("(delay) Calling function %r now!", fn.__name__)
        asyncio.create_task(fn(*args, **kwargs), name=f"delay-{fn.__name__}")  # type: ignore
    except asyncio.CancelledError:
        trace("(delay) Delay for %r cancelled", fn.__name__)
        pass
