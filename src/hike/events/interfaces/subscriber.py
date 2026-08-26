from __future__ import annotations

import typing
from abc import ABC, abstractmethod
from typing import Any, final, overload

from hike.domain_event import DomainEvent
from hike.events.interfaces.background_task import IBackgroundTasks
from hike.events.interfaces.handler import IEventHandler


def _event_type_for[T: DomainEvent](handler: IEventHandler[T]) -> type[T]:
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


# TODO: remove generic type for subscriber(publisher needs the generic! for the RepositoryPublisher)
class IEventSubscriber[T: DomainEvent](ABC):

    @overload
    def subscribe(self, event_handler: IEventHandler[T], /) -> None:
        ...

    @overload
    def subscribe(self, event_type: str, event_handler: IEventHandler[T], /) -> None:
        ...

    @final
    def subscribe(
            self,
            event_type_or_handler: str | IEventHandler[T],
            event_handler: IEventHandler[T] | None = None,
            /,
    ) -> None:
        """multiple calls to this method is supported"""
        if isinstance(event_type_or_handler, str):
            event_cls = _event_type_for(event_handler)  # type: ignore[arg-type]
            self._subscribe(event_type_or_handler, event_cls, event_handler)  # type: ignore[arg-type]
        else:
            event_cls = _event_type_for(event_type_or_handler)
            self._subscribe(event_cls.event_type(), event_cls, event_type_or_handler)

    @abstractmethod
    def _subscribe(self, event_type: str, event_class: type[T], event_handler: IEventHandler[T]) -> None:
        """multiple calls to this method is supported"""


class IExternalEventSubscriber[T: DomainEvent](IEventSubscriber[T], IBackgroundTasks, ABC):
    """Blocking subscriber that consumes events from a broker."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[IEventHandler[T]]] = {}  # this should be like the event bus and for everyone
        self._event_classes: dict[str, type[T]] = {}
        self._seen_ids: dict[str, set[Any]] = {}
        self._running = True

    def cleanup(self) -> None:
        self._running = False

    def _subscribe(self, event_type: str, event_class: type[T], event_handler: IEventHandler[T]) -> None:
        self._handlers.setdefault(event_type, []).append(event_handler)
        self._event_classes[event_type] = event_class  # type: ignore[assignment]
        self._on_subscribe(event_type, event_class)  # type: ignore[arg-type]

    def _deserialize(self, event_type_name: str, data: dict[str, Any]) -> T:
        return self._event_classes[event_type_name].from_dict(data)  # type: ignore[return-value]

    def _dispatch_to_handlers(self, event_type_name: str, event: T) -> None:
        """Dispatch *event* to all registered handlers with deduplication.

        Silently skips the event if its id has already been seen.
        Raises if any handler raises — the caller must nack/not-commit.
        """
        if event.id in self._seen_ids.get(event_type_name, set()):
            return
        for handler in self._handlers.get(event_type_name, []):
            handler.handle(event)
        self._seen_ids.setdefault(event_type_name, set()).add(event.id)

    def _on_subscribe(self, event_type_name: str, event_type: type[T]) -> None:
        """Hook called once per :meth:`subscribe` call.

        Override to perform provider-specific side-effects such as binding a
        broker queue to a routing key.  The default implementation is a no-op.
        """
