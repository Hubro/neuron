"""Higher level interface for asyncio.Event to distribute events"""

import asyncio
import sys


class EventEmitter:
    _events: list[asyncio.Event]
    _events_emitted: int

    def __init__(self) -> None:
        self._events = []
        self._events_emitted = 0

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} "
            + f"num_listeners={self.num_listeners} "
            + f"events_emitted={self.events_emitted}>"
        )

    @property
    def num_listeners(self) -> int:
        return len(self._events)

    @property
    def events_emitted(self) -> int:
        return self._events_emitted

    def event(self) -> asyncio.Event:
        self._prune()

        event = asyncio.Event()
        self._events.append(event)

        return event

    def flag(self) -> asyncio.Event:
        """Like event, but needs to be manually cleared"""
        evt = self.event()
        setattr(evt, "_is_flag", True)
        return evt

    def emit(self):
        for event in self._events:
            event.set()

            if not getattr(event, "_is_flag", False):
                event.clear()

        self._events_emitted += 1

    def _prune(self):
        i = 0

        while i < len(self._events):
            # If all external references to the event are gone, refcount will be 2
            if sys.getrefcount(self._events[i]) == 2:
                self._events.pop(i)
            else:
                i += 1
