from __future__ import annotations

import logging
from typing import Mapping, cast

logging._nameToLevel["TRACE"] = 5
logging._levelToName[5] = "TRACE"


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


logging.setLoggerClass(NeuronLogger)
