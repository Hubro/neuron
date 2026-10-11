import asyncio

import pytest

from neuron import api, block_concurrency, debounce, on_event, on_trigger


async def test_calls_are_skipped_while_running():
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = []

    @block_concurrency
    async def handler(value):
        calls.append(value)
        started.set()
        await finish.wait()

    asyncio.create_task(handler("first"))
    await asyncio.wait_for(started.wait(), timeout=1)

    # Any calls while the first call is still ongoing should be immediately
    # skipped
    async with asyncio.timeout(1):
        await handler("second")
        await handler("third")
        await handler("fourth")

    finish.set()  # Allow the first task to finish

    assert calls == ["first"]


async def test_calls_are_allowed_after_completion():
    @block_concurrency("skip")
    async def handler(value):
        return value

    assert await handler("first") == "first"
    assert await handler("second") == "second"


async def test_calls_are_allowed_after_an_exception():
    @block_concurrency
    async def handler(*, fail=False):
        if fail:
            raise RuntimeError("Something went wrong")
        return "success"

    with pytest.raises(RuntimeError, match="Something went wrong"):
        await handler(fail=True)

    assert await handler() == "success"


async def test_calls_are_allowed_after_cancellation():
    started = asyncio.Event()
    finish = asyncio.Event()

    @block_concurrency
    async def handler():
        started.set()
        await finish.wait()
        return "success"

    async with asyncio.TaskGroup() as tasks:
        running = tasks.create_task(handler())
        await asyncio.wait_for(started.wait(), timeout=1)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

        finish.set()
        assert await handler() == "success"


def test_block_concurrency_must_be_below_on_event(monkeypatch):
    monkeypatch.setattr(api, "_event_handlers", [])

    with pytest.raises(ValueError, match="as a handler"):

        @block_concurrency
        @on_event("button_pressed")
        async def handler():
            pass


def test_block_concurrency_must_be_below_on_trigger(monkeypatch):
    monkeypatch.setattr(api, "_trigger_handlers", [])

    with pytest.raises(ValueError, match="as a handler"):

        @block_concurrency
        @on_trigger({"trigger": "time", "at": "12:00:00"})
        async def handler():
            pass


def test_block_concurrency_must_be_below_debounce():
    with pytest.raises(ValueError, match="swap the order"):

        @block_concurrency
        @debounce(0.1)
        async def handler():
            pass


async def test_debounce_can_wrap_block_concurrency():
    called = asyncio.Event()

    @debounce(0)
    @block_concurrency
    async def handler():
        called.set()

    await handler()
    await asyncio.wait_for(called.wait(), timeout=1)
