"""Higher level interface for asyncio.Event to distribute events"""

import asyncio
import sys


class EventEmitter:
    _events: list[asyncio.Event]

    def __init__(self) -> None:
        self._events = []

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

    def _prune(self):
        i = 0

        while i < len(self._events):
            # If all external references to the event are gone, refcount will be 2
            if sys.getrefcount(self._events[i]) == 2:
                self._events.pop(i)
            else:
                i += 1
