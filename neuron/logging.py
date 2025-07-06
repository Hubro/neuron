from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Mapping, cast

import orjson
import rich.logging

logging._nameToLevel["TRACE"] = 5
logging._levelToName[5] = "TRACE"


def setup_dev_logging():
    """Sets up pretty logging to STDOUT"""

    level = logging.DEBUG if os.environ.get("VERBOSE") else logging.INFO
    level = 5 if os.environ.get("TRACE") else level

    handler = PrettyHandler(rich_tracebacks=True, markup=True)
    handler.setLevel(level)

    logging.basicConfig(
        level="TRACE",
        handlers=[handler],
    )
    logging.getLogger("websockets.client").setLevel(logging.INFO)
    logging.getLogger("watchdog").setLevel(logging.INFO)

    setup_file_logging()


def setup_file_logging():
    """Sets up JSON logging to a local file"""

    from .config import load_config

    suffix = datetime.now().strftime("%Y-%m-%d_%H%M")

    handler = logging.FileHandler(load_config().data_dir / f"neuron_{suffix}.log")
    handler.setFormatter(JSONFormatter())
    get_logger().addHandler(handler)


def get_logger(name: str | None = None) -> NeuronLogger:
    """Returns a logger, just like logging.getLogger

    The type system doesn't understand that we've changed
    the Logger class, so using logging.getLogger breaks.
    """
    return cast(NeuronLogger, logging.getLogger(name))


class NeuronLogger(logging.Logger):
    def trace(
        self,
        msg: object,
        *args: object,
        exc_info: logging._ExcInfoType = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: Mapping[str, object] | None = None,
    ) -> None:
        return super().log(
            5,
            msg,
            *args,
            exc_info=exc_info,
            stack_info=stack_info,
            stacklevel=stacklevel,
            extra=extra,
        )


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return orjson.dumps(record.__dict__, default=lambda x: "?").decode()


class PrettyHandler(rich.logging.RichHandler):
    def format(self, record: logging.LogRecord) -> str:
        from rich.markup import escape

        name = rf"[blue b]\[{record.name}][/]"

        if cmp := getattr(record, "component", None):
            component = rf" [purple b]\[{cmp}][/]"
        else:
            component = ""

        message = " " + record.getMessage()

        if not getattr(record, "markup", False):
            message = escape(message)

        return rf"{name}{component}{message}"


logging.setLoggerClass(NeuronLogger)
