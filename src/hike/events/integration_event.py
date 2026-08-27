import dataclasses
from dataclasses import dataclass
from typing import Any, Self

from hike import Persistable
from hike.domain_event import Event

# integration event implementation from: https://github.com/cloudevents/sdk-python
# TODO: integration event must have event-version style api stability. and should require backward compatibility
# TODO: integration event should be persistable, regular event not
# TODO: how to do the mapping between domain events to integration events
# TODO: how to do validation to incoming integration events
@dataclass(frozen=True, kw_only=True, eq=False)
class IntegrationEvent(Event, Persistable[Any]):
    """Base for all domain eveBasnts.

    Each event carries an auto-generated ``id`` (defaults to a ``uuid4()``)
    that uniquely identifies the event occurrence and serves as the persistence
    key when events are stored via ``IRepository``.  Subclasses may override
    ``id`` with any hashable type.

    Subclasses are plain frozen dataclasses::

        @dataclass(frozen=True)
        class OrderPlaced(DomainEvent):
            order_id: str   # positional; id is keyword-only with a default
    """
    version: int

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
