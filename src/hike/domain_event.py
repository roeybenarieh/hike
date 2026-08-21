import dataclasses
import json

from .common import DomainObject


class DomainEvent(DomainObject): ...


_EVENT_REGISTRY: dict[str, type[DomainEvent]] = {}


def register_event[T: DomainEvent](cls: type[T]) -> type[T]:
    """Register a ``DomainEvent`` subclass so it can be deserialized by name."""
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

# related video: https://www.youtube.com/watch?v=KCvsk5tTP3w
# NOTE: the event bus can either:
# send the event to database in same transaction(?and either way for a confirmation message back, or not wait at all - might be good in a saga)
# send the event to in memory handlers that uses the same transaction(via di), if they failed the producer would fail as well
