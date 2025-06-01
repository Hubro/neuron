import logging
import sys

from .core import Neuron


async def run_neuron():
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
    )
    await Neuron().start()
