from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Any, Self
from uuid import uuid4

from .common import DomainObject
from .persistence.persistable import Persistable


# TODO: integration event must have event-version style api stability. and should require backward compatibility
# TODO: integration event should be persistable, regular event not
# TODO: how to do the mapping between domain events to integration events
# TODO: how to do validation to incoming integration events
@dataclass(frozen=True, kw_only=True, eq=False)
class Event(Persistable[Any]):
    """Base for all domain events.

    Each event carries an auto-generated ``id`` (defaults to a ``uuid4()``)
    that uniquely identifies the event occurrence and serves as the persistence
    key when events are stored via ``IRepository``.  Subclasses may override
    ``id`` with any hashable type.

    Subclasses are plain frozen dataclasses::

        @dataclass(frozen=True)
        class OrderPlaced(DomainEvent):
            order_id: str   # positional; id is keyword-only with a default
    """

    id: Any = field(default_factory=uuid4)
    occurred_at: float = field(default_factory=time.time)

    @classmethod
    def event_type(cls) -> str:
        return cls.__name__

    def get_id(self) -> Any:
        return self.id

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        init_names = {f.name for f in dataclasses.fields(cls) if f.init}
        kwargs: dict[str, Any] = {k: v for k, v in data.items() if k in init_names}
        return cls(**kwargs)

    @classmethod
    def get_init_field_names(cls) -> tuple[str, ...]:
        return tuple(f.name for f in dataclasses.fields(cls) if f.init)  # type: ignore[arg-type]


class DomainEvent(DomainObject, Event):
    ...

# related video: https://www.youtube.com/watch?v=KCvsk5tTP3w
# NOTE: the event bus can either:
# send the event to database in same transaction(?and either way for a confirmation message back, or not wait at all - might be good in a saga)
# send the event to in memory handlers that uses the same transaction(via di), if they failed the producer would fail as well
