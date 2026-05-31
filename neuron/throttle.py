import asyncio
from functools import wraps
from inspect import iscoroutinefunction
from typing import Callable, cast

from neuron.logging import get_logger

LOG = get_logger(__name__)


def throttle(seconds: float):
    """Causes the decorated function to execute immediately if not on cooldown

    After execution, subsequent calls are ignored until the cooldown period passes.
    The cooldown does not refresh on repeated calls.

    Both regular and async functions are supported.

    NB: The decorated function will always return None since it will be
    executed in a separate Task (for async) or scheduled for later (for sync).
    """

    def decorator(fn):
        if iscoroutinefunction(fn):
            return _async_throttle(seconds)(fn)
        else:
            return _sync_throttle(seconds)(fn)

    return decorator


def _sync_throttle(seconds: float):
    cooldown_handle: asyncio.Handle | None = None

    def decorator[T: Callable](fn: T) -> T:
        @wraps(fn)
        def wrapped(*args, **kwargs):
            nonlocal cooldown_handle

            if cooldown_handle:
                LOG.debug(
                    "Ignoring repeated call to %r, still on cooldown", fn.__name__
                )
                return

            LOG.debug(
                "Executing call to %r, starting cooldown for %r seconds",
                fn.__name__,
                seconds,
            )
            fn(*args, **kwargs)

            def clear_cooldown():
                nonlocal cooldown_handle
                cooldown_handle = None

            cooldown_handle = asyncio.get_running_loop().call_later(
                seconds, clear_cooldown
            )

        return cast(T, wrapped)

    return decorator


def _async_throttle(seconds: float):
    cooldown_task: asyncio.Task | None = None

    def decorator[T: Callable](fn: T) -> T:
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            nonlocal cooldown_task

            if cooldown_task:
                LOG.debug(
                    "Ignoring repeated async call to %r, still on cooldown", fn.__name__
                )
                return

            LOG.debug(
                "Executing async call to %r, starting cooldown for %r seconds",
                fn.__name__,
                seconds,
            )

            try:
                await fn(*args, **kwargs)
            except Exception:
                LOG.exception(
                    "Unhandled exception in throttled function %r", fn.__qualname__
                )

            async def cooldown():
                nonlocal cooldown_task
                await asyncio.sleep(seconds)
                cooldown_task = None

            cooldown_task = asyncio.create_task(
                cooldown(), name=f"async_throttle-{fn.__name__}"
            )

        return cast(T, wrapped)

    return decorator
