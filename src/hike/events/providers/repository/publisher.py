from __future__ import annotations

from typing import Iterable, Any

from hike import IRepository
from hike.events.integration_event import IntegrationEvent
from hike.events.interfaces import IEventPublisher


class RepositoryEventPublisher[T: IntegrationEvent, TSessions](IEventPublisher[T]):

    def __init__(self, event_repo: IRepository[Any, T, TSessions]):
        super().__init__()
        self.event_repo = event_repo

    def publish(self, events: Iterable[T]) -> None:
        # TODO: convert events to integration events
        self.event_repo.save_many(events)
