import asyncio
import logging
import signal
import sys
from pathlib import Path

from .core import Neuron
from .logging import get_logger

LOG = get_logger(__name__)


async def run_neuron():
    pythonpath_workaround()

    logging.basicConfig(
        level="TRACE",
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    neuron = Neuron()

    def terminate_handler(signal_number: int, _frame):
        LOG.info(
            "Received %s", "SIGINT" if signal_number is signal.SIGINT else "SIGTERM"
        )
        asyncio.get_running_loop().call_soon_threadsafe(neuron.stop)

    signal.signal(signal.SIGINT, terminate_handler)
    signal.signal(signal.SIGTERM, terminate_handler)

    await neuron.start()


def pythonpath_workaround():
    # Poetry modifies PYTHONPATH, so it can't be set in the Dockerfile
    if Path("/config").is_dir():
        sys.path.append("/config")
