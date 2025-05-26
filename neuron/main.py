from __future__ import annotations

import asyncio
import importlib
import json
import logging
import rich.logging
import websockets
from pathlib import Path
from types import ModuleType

from .config import Config, load_config


LOG = logging.getLogger("neuron")


async def main():
    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[rich.logging.RichHandler(rich_tracebacks=True, markup=True)],
        format=r"[blue b]\[%(name)s][/] %(message)s",
    )

    neuron = Neuron()

    await neuron.start()


class Neuron:
    automations: dict[str, Automation]
    config: Config
    hass_ws: websockets.ClientConnection

    def __init__(self) -> None:
        self.automations = {}
        self.config = load_config()

    async def start(self):
        await self.connect_to_hass()
        self.load_automations()
        await self.init_automations()

        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            LOG.info("Shutting down gracefully")

            for automation in self.automations.values():
                await automation.eject()

            LOG.debug("Closing Home Assistant Websocket connection")
            await self.hass_ws.close()
            await self.hass_ws.wait_closed()

            LOG.info("Bye!")

    async def connect_to_hass(self):
        LOG.info("Connecting to Home Assistant")

        uri = f"ws://{self.config.hass_api_url}/api/websocket"
        LOG.debug("Opening Websocket connection: %s", uri)
        self.hass_ws = await websockets.connect(uri=uri)

        LOG.debug("Waiting for auth request...")
        request = json.loads(await self.hass_ws.recv(decode=True))
        assert request["type"] == "auth_required"

        LOG.debug("Sending auth message...")
        await self.hass_ws.send(
            json.dumps(
                {
                    "type": "auth",
                    "access_token": self.config.hass_api_token,
                }
            )
        )

        LOG.debug("Awaiting response...")
        msg = json.loads(await self.hass_ws.recv(decode=True))

        if msg["type"] == "auth_ok":
            LOG.info("Home Assistant authentication successful")
        elif msg["type"] == "auth_invalid":
            raise RuntimeError("Home Assistant authentication failed, check token")
        else:
            raise RuntimeError("Unexpected response: %r", msg)

    def load_automations(self):
        for package_name in self.config.packages:
            LOG.info("Importing package: %s", package_name)

            try:
                package = importlib.import_module(package_name)
            except ImportError:
                LOG.exception("Failed to import package: %s", package_name)
                continue

            package_path = Path(package.__path__[0])

            for path in package_path.glob("automations/*.py"):
                if path.name == "__init__.py":
                    continue

                module_name = path.stem

                self.automations[package_name] = Automation(
                    f"{package_name}.automations.{module_name}"
                )
                self.automations[package_name].load()

    async def init_automations(self):
        for automation in self.automations.values():
            await automation.init()


class Automation:
    module_name: str
    module: ModuleType
    name: str
    loaded: bool
    initialized: bool

    def __init__(self, module_name: str):
        self.module_name = module_name
        self.name = module_name.split(".")[-1]
        self.loaded = False
        self.initialized = False

    def load(self):
        assert not self.loaded
        assert not self.initialized

        try:
            self.module = importlib.import_module(self.module_name)
        except Exception:
            LOG.exception("Failed to load module %r", self.module_name)

        self.name = getattr(self.module, "NAME", self.module_name)
        self.loaded = True

        LOG.info("Loaded automation: %s", self.module_name)

    async def init(self):
        """Runs the automation's init function"""

        assert self.loaded
        assert not self.initialized

        init = getattr(self.module, "init", None)

        if not init:
            LOG.error("Automation module has no init function: %s", self.module_name)
            return

        LOG.info("Initializing automation: %s", self.module_name)

        await init()  # TODO: Capture event handlers and stuff

        self.initialized = True

    async def eject(self):
        """Undoes init, used for graceful shutdown and code reloading"""

        assert self.initialized

        LOG.info("Ejecting automation: %s", self.module_name)

        # TODO: <-

        self.initialized = False

    async def reload(self):
        """Reloads the automation module from source and reinitializes it"""

        assert self.loaded
        assert self.initialized

        await self.eject()

        LOG.info("Reloading module: %s", self.module_name)
        importlib.reload(self.module)

        await self.init()


if __name__ == "__main__":
    asyncio.run(main())
