from dataclasses import dataclass
from typing import Any

from hike.persistence.persistable import DataclassPersistable
from hike.domain_event import DomainEvent

# integration event implementation from: https://github.com/cloudevents/sdk-python
# TODO: integration event must have event-version style api stability. and should require backward compatibility
# TODO: integration event should be persistable, regular event not
# TODO: how to do the mapping between domain events to integration events
# TODO: how to do validation to incoming integration events
@dataclass(frozen=True, kw_only=True, eq=False)
class IntegrationEvent(DomainEvent, DataclassPersistable[Any]):
    """Base for all integration events.

    Each event carries an auto-generated ``id`` (defaults to a ``uuid4()``)
    that uniquely identifies the event occurrence and serves as the persistence
    key when events are stored via ``IRepository``.  Subclasses may override
    ``id`` with any hashable type.

    Subclasses are plain frozen dataclasses::

        @dataclass(frozen=True)
        class OrderPlaced(IntegrationEvent):
            order_id: str   # positional; id is keyword-only with a default
    """
    version: int = 1

    def get_id(self) -> Any:
        return self.id
