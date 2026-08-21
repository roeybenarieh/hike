import typing

from hike import DomainEvent
from hike.events.interfaces import IEventHandler


def event_type_for[T: DomainEvent](handler: IEventHandler[T]) -> type[T]:
    # Primary: IEventHandler[ConcreteType] in the class hierarchy.
    for cls in type(handler).__mro__:
        for base in getattr(cls, "__orig_bases__", ()):
            origin = typing.get_origin(base)
            if origin is None or not issubclass(origin, IEventHandler):
                continue
            args = typing.get_args(base)
            if args and not isinstance(args[0], typing.TypeVar):
                return args[0]  # type: ignore[return-value]

    # Fallback: infer from the 'event' parameter of handle().
    # Needed when IReversibleEventHandler is used without a type argument.
    try:
        hints = typing.get_type_hints(type(handler).handle)
        event_type = hints.get("event")
        if isinstance(event_type, type) and issubclass(event_type, DomainEvent) and event_type is not DomainEvent:
            return event_type  # type: ignore[return-value]
    except Exception:
        pass

    raise TypeError(
        f"{type(handler).__name__} must specify an event type via IEventHandler[T]"
    )
