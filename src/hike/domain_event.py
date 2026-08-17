import dataclasses
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from typing import Any, cast

from .common import DomainObject


class DomainEvent(DomainObject): ...


_EVENT_REGISTRY: dict[str, type[DomainEvent]] = {}


def register_event(cls: type[DomainEvent]) -> type[DomainEvent]:
    """Register a ``DomainEvent`` subclass for outbox/inbox deserialization.

    Required when using the outbox pattern. Events must also be ``@dataclass``.
    """
    _EVENT_REGISTRY[cls.__name__] = cls
    return cls


def serialize_event(event: DomainEvent) -> tuple[str, str]:
    """Return ``(event_type_name, json_str)``. The event must be a ``@dataclass``."""
    return type(event).__name__, json.dumps(dataclasses.asdict(event), default=str)  # type: ignore[arg-type]


def deserialize_event(event_type: str, json_data: str) -> DomainEvent:
    """Reconstruct a ``DomainEvent`` from its type name and JSON payload.

    The event class must have been registered via ``@register_event``.
    """
    cls = _EVENT_REGISTRY[event_type]
    return cls(**json.loads(json_data))


class EventHandler[TEvent: DomainEvent](ABC):
    """Object-style event handler.

    Implement ``handle(event)`` and subscribe an instance via
    ``bus.subscribe(MyEvent, handler_instance)``.

    Handler objects are callable — ``bus.publish()`` invokes ``handler(event)``
    which delegates to ``handle(event)``.
    """

    @abstractmethod
    def handle(self, event: TEvent) -> None: ...

    @abstractmethod
    def compensate(self, event: TEvent) -> None: ...

    def __call__(self, event: TEvent) -> None:
        self.handle(event)


class EventBus(ABC):
    """Publish/subscribe interface for domain events.

    Handlers are registered per event type via ``subscribe``; all matching
    handlers are invoked synchronously by ``publish``.
    """

    @abstractmethod
    def subscribe[TEvent: DomainEvent](
        self,
        event_type: type[TEvent],
        handler: Callable[[TEvent], None] | EventHandler[TEvent],
    ) -> None: ...

    @abstractmethod
    def publish(self, event: DomainEvent) -> None: ...

    def publish_all(self, events: Iterable[DomainEvent]) -> None:
        for event in events:
            self.publish(event)

    def drain(self, *aggregates: Any) -> None:
        """Publish and clear events from each aggregate immediately."""
        for agg in aggregates:
            self.publish_all(agg.get_events())
            agg.clear_events()


class InMemoryEventBus(EventBus):
    """Synchronous in-memory bus — suitable for testing and simple applications."""

    def __init__(self) -> None:
        self._handlers: dict[type[DomainEvent], list[Callable[[Any], None]]] = {}

    def subscribe[TEvent: DomainEvent](
        self,
        event_type: type[TEvent],
        handler: Callable[[TEvent], None] | EventHandler[TEvent],
    ) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def publish(self, event: DomainEvent) -> None:
        for handler in self._handlers.get(type(event), []):
            handler(event)

    def publish_all(self, events: Iterable[DomainEvent]) -> None:
        succeeded: list[tuple[DomainEvent, EventHandler[Any]]] = []
        for event in list(events):
            for handler in self._handlers.get(type(event), []):
                try:
                    handler(event)
                    if isinstance(handler, EventHandler):
                        succeeded.append((event, cast("EventHandler[Any]", handler)))
                except BaseException:
                    for past_event, past_handler in reversed(succeeded):
                        try:
                            past_handler.compensate(past_event)
                        except Exception:
                            logging.exception(
                                "Compensation failed for %s on %s",
                                type(past_handler).__name__,
                                type(past_event).__name__,
                            )
                    raise
