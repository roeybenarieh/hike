from __future__ import annotations

from typing import Any, Iterable

from hike import IRepository, UnitOfWork
from hike.events.integration_event import IntegrationEvent
from hike.events.interfaces.event_bus import IExternalEventBus
from hike.events.providers.repository.publisher import RepositoryEventPublisher
from hike.events.providers.repository.subscriber import RepositoryEventSubscriber


class RepositoryEventBus[T: IntegrationEvent, TSessions](
    RepositoryEventSubscriber[T, TSessions],
    IExternalEventBus[T],
):
    """Combined publisher + subscriber backed by a repository (outbox pattern).

    Persists events via :class:`RepositoryEventPublisher` and watches for them
    via :class:`RepositoryEventSubscriber`.  Both sides share the same
    *event_repo* and *event_uow*.
    """

    def __init__(
            self,
            event_repo: IRepository[Any, T, TSessions],
            event_uow: UnitOfWork[TSessions],
    ) -> None:
        super().__init__(event_repo, event_uow)
        self._publisher: RepositoryEventPublisher[T, TSessions] = RepositoryEventPublisher(event_repo)

    def publish(self, events: Iterable[T]) -> None:
        self._publisher.publish(events)
