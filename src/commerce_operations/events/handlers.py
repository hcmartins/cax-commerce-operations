from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from commerce_operations.events.types import EventEnvelope, EventPayload, EventType

EventHandler = Callable[[EventEnvelope[EventPayload], Session], None]


@dataclass(frozen=True)
class RegisteredHandler:
    name: str
    event_type: EventType
    callback: EventHandler


class EventHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[EventType, list[RegisteredHandler]] = defaultdict(list)
        self._names: set[str] = set()

    def register(self, event_type: EventType, name: str, callback: EventHandler) -> None:
        if name in self._names:
            raise ValueError(f"Event handler name must be globally unique: {name}")
        self._names.add(name)
        self._handlers[event_type].append(RegisteredHandler(name, event_type, callback))

    def handlers_for(self, event_type: EventType) -> Sequence[RegisteredHandler]:
        return tuple(self._handlers.get(event_type, ()))
