import asyncio

from .throttle import throttle


async def test_sync_throttle():
    x = 0

    @throttle(seconds=0.01)
    def fn(i):
        nonlocal x
        x += i

    fn(1)
    fn(1)
    fn(1)
    fn(1)
    fn(1)

    assert x == 1

    await asyncio.sleep(0.1)

    fn(1)
    fn(1)
    fn(1)

    assert x == 2


async def test_async_throttle():
    x = 0

    @throttle(seconds=0.01)
    async def fn(i):
        nonlocal x
        x += i

    await fn(1)
    await fn(1)
    await fn(1)
    await fn(1)
    await fn(1)

    assert x == 1

    await asyncio.sleep(0.1)

    await fn(1)
    await fn(1)
    await fn(1)

    assert x == 2
