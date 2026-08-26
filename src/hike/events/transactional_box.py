from typing import Any, Iterable

from hike import DomainEvent
from hike.events.event_repository import RepositoryEventSubscriber, RepositoryEventPublisher
from hike.events.interfaces import IEventPublisher, IExternalEventSubscriber, IEventBus, IEventHandler
from hike.events.interfaces.background_task import Task, IBackgroundTasks
from hike.events.interfaces.publisher import IExternalEventPublisher


class OutboxEventPublisher[T: DomainEvent, TSession](IExternalEventPublisher[T]):
    def __init__(
            self,
            repo_publisher: RepositoryEventPublisher[T, TSession],
            repo_subscriber: RepositoryEventSubscriber[T, TSession],
            broker_publisher: IEventPublisher[T]
    ):
        self.repo_publisher = repo_publisher
        self.repo_subscriber = repo_subscriber
        self.repo_subscriber.subscribe(broker_publisher)

    def publish(self, events: Iterable[T]) -> None:
        self.repo_publisher.publish(events)

    def cleanup(self) -> None:
        self.repo_subscriber.cleanup()

    def tasks(self) -> list[Task]:
        return [self.repo_subscriber.start]


class InboxEventSubscriber[T: DomainEvent, TSession](IExternalEventSubscriber[T]):
    def __init__(
            self,
            broker_subscriber: IExternalEventSubscriber[T],
            repo_publisher: RepositoryEventPublisher[T, TSession],
            repo_subscriber: RepositoryEventSubscriber[T, TSession],
    ):
        super().__init__()
        self.broker_subscriber = broker_subscriber
        self.repo_publisher = repo_publisher
        self.repo_subscriber = repo_subscriber

    def cleanup(self) -> None:
        self.broker_subscriber.cleanup()
        self.repo_subscriber.cleanup()

    def tasks(self) -> list[Task]:
        # FIX: maybe return callables and not run them in here
        return self.broker_subscriber.tasks() + self.repo_subscriber.tasks()


# integration event implementation from: https://github.com/cloudevents/sdk-python
# create mechanism for mapping domain event to integration event
# features from: https://github.com/adimiko/TransactionalBox
class TransactionalBox(IEventBus, IBackgroundTasks):
    def __init__(self, inbox: InboxEventSubscriber[Any, Any], outbox: OutboxEventPublisher[Any, Any]):
        self.inbox = inbox
        self.outbox = outbox

    def publish(self, events: Iterable[DomainEvent]) -> None:
        self.outbox.publish(events)

    def _subscribe(self, event_type: str, event_class: type, event_handler: IEventHandler[Any]) -> None:
        self.inbox.subscribe(event_type, event_handler)

    def cleanup(self) -> None:
        self.inbox.cleanup()
        self.outbox.cleanup()

    def tasks(self) -> list[Task]:
        return self.inbox.tasks() + self.outbox.tasks()
