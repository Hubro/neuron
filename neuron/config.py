from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

CONFIG_PATH = os.environ.get("NEURON_CONFIG_PATH", "/data/options.json")
DATA_DIR = os.environ.get("NEURON_DATA_DIR", "/config")
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
    data_dir: Path
    hass_websocket_uri: str
    hass_api_token: str


@cache
def load_config():
    with open(CONFIG_PATH) as f:
        raw_config = json.load(f)
        LOG.info("Loaded config: %r", raw_config)

    return Config(
        packages=raw_config["packages"],
        data_dir=Path(DATA_DIR),
        hass_websocket_uri=HASS_WEBSOCKET_URI,
        hass_api_token=HASS_API_TOKEN,
    )
