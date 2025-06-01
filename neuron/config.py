from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from functools import cache

CONFIG_PATH = os.environ.get("NEURON_CONFIG_PATH", "/data/options.json")
HASS_WEBSOCKET_URI = os.environ.get(
    "HASS_WEBSOCKET_URI", "ws://supervisor/core/websocket"
)
HASS_API_TOKEN = (
    os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HOME_ASSISTANT_TOKEN") or ""
)
assert HASS_API_TOKEN, "Home Assistant API token not set"

LOG = logging.getLogger(__name__)


@dataclass
class Config:
    packages: list[str]

    @property
    def hass_websocket_uri(self):
        return HASS_WEBSOCKET_URI

    @property
    def hass_api_token(self):
        return HASS_API_TOKEN


@cache
def load_config():
    with open(CONFIG_PATH) as f:
        raw_config = json.load(f)
        LOG.info("Loaded config: %r", raw_config)

    return Config(
        packages=raw_config["packages"],
    )
