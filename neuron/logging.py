from __future__ import annotations

import logging
import os
from typing import Mapping, cast

import rich.logging

logging._nameToLevel["TRACE"] = 5
logging._levelToName[5] = "TRACE"


def setup_dev_logging():
    """Sets up pretty logging to STDOUT"""

    level = logging.DEBUG if os.environ.get("VERBOSE") else logging.INFO
    level = 5 if os.environ.get("TRACE") else level

    logging.basicConfig(
        level=level,
        handlers=[PrettyHandler(rich_tracebacks=True, markup=True)],
    )
    logging.getLogger("websockets.client").setLevel(logging.INFO)
    logging.getLogger("watchdog").setLevel(logging.INFO)


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


class PrettyHandler(rich.logging.RichHandler):
    def format(self, record: logging.LogRecord) -> str:
        from rich.markup import escape

        name = rf"[blue b]\[{record.name}][/]"

        if cmp := getattr(record, "component", None):
            component = rf" [purple b]\[{cmp}][/]"
        else:
            component = ""

        message = f" {escape(record.getMessage())}"

        return rf"{name}{component}{message}"


logging.setLoggerClass(NeuronLogger)
