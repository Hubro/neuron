import asyncio
from asyncio import iscoroutinefunction
from functools import wraps
from typing import Callable, Coroutine

from neuron.logging import get_logger

LOG = get_logger(__name__)


def debounce(seconds: float):
    """Causes the decorated function to execute after a delay

    The delay is reset for every subsequent call, ensuring that the function is
    only executed once even if it's called multiple times in rapid succession.

    Both regular and async functions are supported.

    NB: The decorated function will always return None since it will be
    executed in a separate Task.
    """

    def decorator(fn):
        if iscoroutinefunction(fn):
            return _async_debounce(seconds)(fn)
        else:
            return _sync_debounce(seconds)(fn)

    return decorator


def _sync_debounce(seconds: float):
    handle: asyncio.Handle | None = None

    def decorator(fn: Callable):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            nonlocal handle

            def call():
                nonlocal handle
                handle = None
                fn(*args, **kwargs)

            if handle:
                LOG.debug("Got repeated call to %r, resetting delay timer", fn.__name__)
                handle.cancel()
            else:
                LOG.debug("Delaying call to %r for %r seconds", fn.__name__, seconds)

            handle = asyncio.get_running_loop().call_later(seconds, call)

        return wrapped

    return decorator


def _async_debounce(seconds: float):
    task: asyncio.Task | None = None

    def decorator(fn: Callable[..., Coroutine]):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            nonlocal task

            async def call():
                nonlocal task

                await asyncio.sleep(seconds)

                task = None
                await fn(*args, **kwargs)

            if task:
                LOG.debug(
                    "Got repeated async call to %r, resetting delay timer", fn.__name__
                )
                task.cancel()
            else:
                LOG.debug(
                    "Delaying async call to %r for %r seconds", fn.__name__, seconds
                )

            task = asyncio.create_task(call(), name=f"async_debounce-{fn.__name__}")

        return wrapped

    return decorator
