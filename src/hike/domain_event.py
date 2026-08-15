from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from typing import Any

from .common import DomainObject


class DomainEvent(DomainObject): ...


class EventBus(ABC):
    """Publish/subscribe interface for domain events.

    Handlers are registered per event type via ``subscribe``; all matching
    handlers are invoked synchronously by ``publish``.
    """

    @abstractmethod
    def subscribe[TEvent: DomainEvent](
        self,
        event_type: type[TEvent],
        handler: Callable[[TEvent], None],
    ) -> None: ...

    @abstractmethod
    def publish(self, event: DomainEvent) -> None: ...

    def publish_all(self, events: Iterable[DomainEvent]) -> None:
        for event in events:
            self.publish(event)


class InMemoryEventBus(EventBus):
    """Synchronous in-memory bus — suitable for testing and simple applications."""

    def __init__(self) -> None:
        self._handlers: dict[type[DomainEvent], list[Callable[[Any], None]]] = {}

    def subscribe[TEvent: DomainEvent](
        self,
        event_type: type[TEvent],
        handler: Callable[[TEvent], None],
    ) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def publish(self, event: DomainEvent) -> None:
        for handler in self._handlers.get(type(event), []):
            handler(event)
