from __future__ import annotations

import logging
import os
import sys
import threading
from contextlib import contextmanager
from datetime import datetime
from queue import Queue
from typing import Mapping, cast

import orjson
import requests
import rich.logging

from .config import load_config

logging._nameToLevel["TRACE"] = 5
logging._levelToName[5] = "TRACE"


def setup_dev_logging():
    """Sets up pretty logging to STDOUT"""

    config = load_config()

    setup_pretty_stdout_logging()

    setup_file_logging()

    if config.victoria_logs_uri:
        logging.root.addHandler(VictoriaLogsHandler(config.victoria_logs_uri))

        for handler in logging.root.handlers:
            handler.addFilter(VictoriaLogFilter())


def setup_pretty_stdout_logging():
    level = logging.DEBUG if os.environ.get("VERBOSE") else logging.INFO
    level = 5 if os.environ.get("TRACE") else level

    handler = PrettyHandler(rich_tracebacks=True, markup=True)
    handler.setLevel(level)

    logging.root.level = 0
    logging.root.addHandler(handler)

    # Reduce noise while developing
    if os.environ.get("WS_VERBOSE"):
        logging.getLogger("websockets.client").setLevel(logging.DEBUG)
    else:
        logging.getLogger("websockets.client").setLevel(logging.INFO)

    logging.getLogger("watchdog").setLevel(logging.INFO)


def setup_prod_logging():
    config = load_config()

    logging.basicConfig(
        level="TRACE",
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    setup_file_logging()

    if config.victoria_logs_uri:
        logging.root.addHandler(VictoriaLogsHandler(config.victoria_logs_uri))

        for handler in logging.root.handlers:
            handler.addFilter(VictoriaLogFilter())


def setup_file_logging():
    """Sets up JSON logging to a local file"""

    from .config import load_config

    config = load_config()

    file_log_path = config.json_log_file

    if not file_log_path:
        suffix = datetime.now().strftime("%Y-%m-%d_%H%M")
        file_log_path = config.data_dir / f"neuron_{suffix}.log"

    if not file_log_path.parent.exists():
        try:
            file_log_path.parent.mkdir()
        except Exception:
            logging.root.exception(
                f"Log dir {file_log_path.parent!r} doesn't exist and could not be created"
            )
            return

    try:
        handler = logging.FileHandler(file_log_path)
    except Exception:
        logging.root.exception("Failed to set up file logging")
        return

    handler.setFormatter(JSONFormatter())
    logging.root.addHandler(handler)


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
            stacklevel=stacklevel + 1,
            extra=extra,
        )


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return orjson.dumps(
            record.__dict__ | {"message": record.getMessage()},
            default=lambda x: "?",
        ).decode()


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


class VictoriaLogsHandler(logging.Handler):
    """Ships JSON log lines to VictoriaLogs"""

    def __init__(self, uri: str) -> None:
        self.uri = uri

        self.session: requests.Session | None = None
        self.level = logging.NOTSET

        self.queue = Queue()
        self.thread = threading.Thread(
            target=self.daemon, name="victoria-logs-handler", daemon=True
        )
        self.thread.start()

        super().__init__()

    def daemon(self):
        if self.session is None:
            self.session = requests.Session()

        while True:
            try:
                self.push_log_line(self.queue.get())

            except Exception as e:
                get_logger(__name__).warning(
                    "Failed to push log line to VictoriaLogs",
                    exc_info=e,
                    extra={"source": "VictoriaLogsHandler"},
                )

    def push_log_line(self, log_line: dict):
        json_log_line = orjson.dumps(
            log_line,
            default=lambda x: "?",
        )

        assert self.session is not None
        response = self.session.post(
            f"{self.uri}/insert/jsonline",
            data=json_log_line,
            headers={
                "Content-Type": "application/stream+json",
                "VL-Msg-Field": "message",
                "VL-Time-Field": "created",
            },
            timeout=1,
        )
        response.raise_for_status()

    def emit(self, record: logging.LogRecord) -> None:
        # Skip logs produced by this handler
        if getattr(record, "source", "") == "VictoriaLogsHandler":
            return

        # Skip logs from requests
        if record.name.startswith("requests"):
            return

        self.queue.put(record.__dict__ | {"message": record.getMessage()})


class VictoriaLogFilter(logging.Filter):
    """Filters out log lines from the victoria log handler thread to prevent loops"""

    def filter(self, record: logging.LogRecord):
        if (
            "victoria-logs-handler" in (record.threadName or "")
        ) and record.levelno < logging.WARNING:
            return False

        return True


logging.setLoggerClass(NeuronLogger)
