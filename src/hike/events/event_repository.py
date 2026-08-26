from typing import Any, Iterable, NoReturn

from hike import DomainEvent, IRepository, UnitOfWork
from hike.events.interfaces import IEventPublisher
from hike.events.interfaces.background_task import Task
from hike.events.interfaces.subscriber import IExternalEventSubscriber


class RepositoryEventSubscriber[T: DomainEvent, TSessions](IExternalEventSubscriber[T]):
    def __init__(
            self,
            event_repo: IRepository[Any, T, TSessions],
            event_uow: UnitOfWork[TSessions]
    ):
        super().__init__()
        self.event_repo = event_repo
        self.event_uow = event_uow

    def cleanup(self) -> None:
        ...

    def start(self) -> NoReturn:
        for event in self.event_repo.watch():
            for handler in self._handlers.get(event.event_type(), []):
                handler.handle(event)

            # all handlers finished successfully, deleting the event
            with self.event_uow(self.event_repo, auto_commit=True):
                self.event_repo.delete(event)
        raise RuntimeError("watch() terminated unexpectedly")

    def tasks(self) -> list[Task]:
        return [self.start]


class RepositoryEventPublisher[T: DomainEvent, TSessions](IEventPublisher[T]):

    def __init__(self, event_repo: IRepository[Any, T, TSessions]):
        super().__init__()
        self.event_repo = event_repo

    def publish(self, events: Iterable[T]) -> None:
        # TODO: convert events to integration events
        self.event_repo.save_many(events)
