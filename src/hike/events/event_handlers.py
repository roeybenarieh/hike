from abc import ABC
from typing import Any

from hike import IAggregateRepository
from hike.aggregate import Aggregate as _Aggregate
from hike.domain_event import DomainEvent
from hike.events.interfaces import IEventHandler


class DomainEventHandler[TId, TAggregate: _Aggregate[Any], TDomainEvent: DomainEvent](IEventHandler[TDomainEvent], ABC):
    """This event handler is not reversible by itself,
    the injected repo should be in the same session that raised the event"""

    def __init__(self, repo: IAggregateRepository[TId, TAggregate, Any]) -> None:
        self.repo = repo
