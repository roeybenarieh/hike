from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field as _dc_field
from typing import Any, Self, cast
from uuid import UUID, uuid4

from .common import DomainObject
from .entity import EntityID
from .persistence.persistable import Persistable


# TODO: create proper serialize/deserialize functionality
@dataclass(frozen=True, kw_only=True, eq=False)
class DomainEvent(DomainObject, Persistable[UUID]):
    """Base for all domain events.

    Each event carries an auto-generated ``id: EntityID[UUID]`` that uniquely
    identifies the event occurrence and serves as the persistence key when
    events are stored via ``IRepository``.

    Subclasses are plain frozen dataclasses::

        @dataclass(frozen=True)
        class OrderPlaced(DomainEvent):
            order_id: str   # positional; id is keyword-only with a default
    """

    id: EntityID[UUID] = _dc_field(default_factory=lambda: EntityID(uuid4()))

    def get_id(self) -> UUID:
        return self.id.value

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for f in dataclasses.fields(self):
            val: Any = getattr(self, f.name)
            result[f.name] = cast(Any, val).value if isinstance(val, EntityID) else val
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        init_names = {f.name for f in dataclasses.fields(cls) if f.init}
        kwargs: dict[str, Any] = {k: v for k, v in data.items() if k in init_names}
        raw_id = kwargs.get("id")
        if raw_id is not None and not isinstance(raw_id, EntityID):
            kwargs["id"] = EntityID(UUID(str(raw_id)))
        return cls(**kwargs)

    @classmethod
    def get_init_field_names(cls) -> tuple[str, ...]:
        return tuple(f.name for f in dataclasses.fields(cls) if f.init)  # type: ignore[arg-type]


_EVENT_REGISTRY: dict[str, type[DomainEvent]] = {}


def register_event[T: DomainEvent](cls: type[T]) -> type[T]:
    """Register a ``DomainEvent`` subclass so it can be deserialized by name."""
    _EVENT_REGISTRY[cls.__name__] = cls
    return cls


def serialize_event(event: DomainEvent) -> tuple[str, str]:
    """Return ``(event_type_name, json_str)``.

    ``id`` is serialized as a plain UUID string; all other fields are
    converted with ``str()`` as a fallback for non-JSON types.
    """
    import dataclasses

    raw: dict[str, Any] = dataclasses.asdict(event)
    # Flatten EntityID wrapper produced by asdict ({"value": uuid}) → raw UUID string.
    if "id" in raw and isinstance(raw["id"], dict) and "value" in raw["id"]:
        raw["id"] = raw["id"]["value"]
    return type(event).__name__, json.dumps(raw, default=str)


def deserialize_event(event_type: str, json_data: str) -> DomainEvent:
    """Reconstruct a ``DomainEvent`` from its type name and JSON payload.

    The event class must have been registered via ``@register_event``.
    """
    cls = _EVENT_REGISTRY[event_type]
    data: dict[str, Any] = json.loads(json_data)
    if "id" in data:
        data["id"] = EntityID(UUID(str(data["id"])))
    return cls(**data)

# related video: https://www.youtube.com/watch?v=KCvsk5tTP3w
# NOTE: the event bus can either:
# send the event to database in same transaction(?and either way for a confirmation message back, or not wait at all - might be good in a saga)
# send the event to in memory handlers that uses the same transaction(via di), if they failed the producer would fail as well
