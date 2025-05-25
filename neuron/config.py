from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

CONFIG_PATH = os.environ.get("NEURON_CONFIG_PATH", "/data/options.json")

LOG = logging.getLogger(__name__)


@dataclass
class Config:
    packages: list[str]
    hass_api_url: str


def load_config():
    global config

    with open(CONFIG_PATH) as f:
        raw_config = json.load(f)
        LOG.info("Loaded config: %r", raw_config)

    return Config(
        packages=raw_config["packages"],
        hass_api_url=raw_config["hass_api_url"],
    )
