from __future__ import annotations

import typing
from abc import ABC, abstractmethod
from typing import Any

from hike.domain_event import DomainEvent
from hike.events.interfaces.background_task import BackgroundTask
from hike.events.interfaces.handler import IEventHandler


def event_type_for[T: DomainEvent](handler: IEventHandler[T]) -> type[T]:
    """Return the concrete ``DomainEvent`` subclass *handler* is typed for."""
    for cls in type(handler).__mro__:
        for base in getattr(cls, "__orig_bases__", ()):
            origin = typing.get_origin(base)
            if origin is None or not (isinstance(origin, type) and issubclass(origin, IEventHandler)):
                continue
            args = typing.get_args(base)
            if args and not isinstance(args[0], typing.TypeVar):
                return args[0]  # type: ignore[return-value]

    try:
        hints = typing.get_type_hints(type(handler).handle)
        event_type = hints.get("event")
        if isinstance(event_type, type) and issubclass(event_type, DomainEvent) and event_type is not DomainEvent:
            return event_type  # type: ignore[return-value]
    except Exception:
        pass

    raise TypeError(f"{type(handler).__name__} must specify an event type via IEventHandler[T]")


class IEventSubscriber[T: DomainEvent](ABC):

    @abstractmethod
    def subscribe(self, event_handler: IEventHandler[T]) -> None:
        """multiple calls to this method is supported"""


class IBrokerEventSubscriber(IEventSubscriber[DomainEvent], BackgroundTask, ABC):
    """Blocking subscriber that consumes events from a broker.

    Implementations must uphold three contracts:

    **Ack/nack**: a message is only removed from the broker after the handler
    completes successfully.  If the handler raises, the message must be nacked
    so it remains in the broker and can be retried.

    **Exclusive in-flight delivery with failover**: all instances of the same
    subscriber class share a single broker resource (queue or consumer group)
    and act as competing consumers.  When one instance receives a message, no
    other instance can receive that same message while it is in-flight
    (between receive and ack/nack).  If the receiving instance crashes before
    acking, the broker automatically makes the message available again so
    another running instance can pick it up — RabbitMQ requeues the unacked
    delivery, Kafka rebalances the partition, and Redis Streams exposes the
    stranded message via ``XAUTOCLAIM`` after a configurable idle timeout.

    **Deduplication by event id**: if the broker delivers the same event id
    more than once (e.g. due to a retry), only one delivery must be passed to
    the handlers — subsequent duplicates must be silently dropped.

    Subclasses must call ``super().__init__()`` to initialise shared state.
    Override :meth:`_on_subscribe` for provider-specific side-effects (e.g.
    binding a broker queue to a routing key).
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[IEventHandler[DomainEvent]]] = {}
        self._event_classes: dict[str, type[DomainEvent]] = {}
        self._seen_ids: set[Any] = set()

    def subscribe(self, event_handler: IEventHandler[DomainEvent]) -> None:
        event_type = event_type_for(event_handler)  # type: ignore[arg-type]
        name = event_type.__name__
        self._handlers.setdefault(name, []).append(event_handler)
        self._event_classes[name] = event_type  # type: ignore[assignment]
        self._on_subscribe(name, event_type)  # type: ignore[arg-type]

    def _on_subscribe(self, event_type_name: str, event_type: type[DomainEvent]) -> None:
        """Hook called once per :meth:`subscribe` call.

        Override to perform provider-specific side-effects such as binding a
        broker queue to a routing key.  The default implementation is a no-op.
        """


IBlockingEventSubscriber = IBrokerEventSubscriber
