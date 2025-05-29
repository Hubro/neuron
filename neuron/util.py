import orjson
from typing import Any


def stringify(obj: Any) -> str:
    return orjson.dumps(obj, option=orjson.OPT_SORT_KEYS).decode()
