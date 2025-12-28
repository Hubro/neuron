from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

CONFIG_PATH = os.environ.get("NEURON_CONFIG_PATH", "/data/options.json")
DATA_DIR = os.environ.get("NEURON_DATA_DIR", "/config")
DEV = bool(os.environ.get("NEURON_DEV", ""))
JSON_LOG_FILE = os.environ.get("NEURON_JSON_LOG_FILE")
VICTORIA_LOGS_URI = os.environ.get("NEURON_VICTORIA_LOGS_URI")
AUTOMATION_PACKAGES = os.environ.get("NEURON_AUTOMATION_PACKAGES", None)
HASS_WEBSOCKET_URI = os.environ.get(
    "HASS_WEBSOCKET_URI", "ws://supervisor/core/websocket"
)
HASS_API_TOKEN = (
    os.environ.get("HOME_ASSISTANT_TOKEN") or os.environ.get("SUPERVISOR_TOKEN") or ""
)
assert HASS_API_TOKEN, "Home Assistant API token not set"

LOG = logging.getLogger(__name__)


@dataclass
class Config:
    packages: list[str]
    data_dir: Path
    dev: bool
    json_log_file: Path | None
    victoria_logs_uri: str | None
    hass_websocket_uri: str
    hass_api_token: str


@cache
def load_config():
    with open(CONFIG_PATH) as f:
        raw_config = json.load(f)
        LOG.debug("Loaded JSON config: %r", raw_config)

    if AUTOMATION_PACKAGES:
        packages = AUTOMATION_PACKAGES.split(",")
    else:
        packages = raw_config["packages"]

    config = Config(
        packages=packages,
        data_dir=Path(DATA_DIR),
        dev=DEV,
        json_log_file=Path(JSON_LOG_FILE) if JSON_LOG_FILE else None,
        victoria_logs_uri=VICTORIA_LOGS_URI,
        hass_websocket_uri=HASS_WEBSOCKET_URI,
        hass_api_token=HASS_API_TOKEN,
    )
    LOG.info("Loaded config: %r", config)

    return config
