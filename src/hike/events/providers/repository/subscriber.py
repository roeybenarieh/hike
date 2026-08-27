from typing import Any, NoReturn

from hike import IRepository, UnitOfWork
from hike.events.integration_event import IntegrationEvent
from hike.events.interfaces.background_task import Task
from hike.events.interfaces.subscriber import IExternalEventSubscriber


class RepositoryEventSubscriber[TEvent: IntegrationEvent, TSessions](IExternalEventSubscriber[TEvent]):
    def __init__(
            self,
            event_repo: IRepository[Any, TEvent, TSessions],
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
