from __future__ import annotations

import asyncio
import logging
import os

import rich.logging

from .core import Neuron


LOG = logging.getLogger("neuron")


async def main():
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("VERBOSE") else logging.INFO,
        handlers=[rich.logging.RichHandler(rich_tracebacks=True, markup=True)],
        format=r"[blue b]\[%(name)s][/] %(message)s",
    )
    logging.getLogger("websockets.client").setLevel(logging.INFO)

    neuron = Neuron()

    await neuron.start()


if __name__ == "__main__":
    asyncio.run(main())
