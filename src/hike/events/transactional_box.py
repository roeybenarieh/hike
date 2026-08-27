from typing import Any, Iterable

from hike.events.integration_event import IntegrationEvent
from hike.events.interfaces import IEventPublisher, IExternalEventSubscriber, IEventBus, IEventHandler
from hike.events.interfaces.background_task import Task, IBackgroundTasks
from hike.events.interfaces.publisher import IExternalEventPublisher
from hike.events.providers.repository import RepositoryEventPublisher, RepositoryEventSubscriber


class OutboxEventPublisher[TSession](IExternalEventPublisher[IntegrationEvent]):
    """contract:
    1. No guarantee of message order between services
    2. Support for multiple running instances
    3. sends a message AT LEAST ONE
    """

    def __init__(
            self,
            repo_publisher: RepositoryEventPublisher[IntegrationEvent, TSession],
            repo_subscriber: RepositoryEventSubscriber[IntegrationEvent, TSession],
            broker_publisher: IEventPublisher[IntegrationEvent],
    ):
        self.repo_publisher = repo_publisher
        self.repo_subscriber = repo_subscriber
        self.repo_subscriber.subscribe(broker_publisher)

    def publish(self, events: Iterable[IntegrationEvent]) -> None:
        self.repo_publisher.publish(events)

    def cleanup(self) -> None:
        self.repo_subscriber.cleanup()

    def tasks(self) -> list[Task]:
        return [self.repo_subscriber.start]


class InboxEventSubscriber[T: IntegrationEvent, TSession](IExternalEventSubscriber[T]):
    """contract:
    1. No guarantee of message order between services
    2. Support for multiple running instances
    """

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

    def _subscribe(self, event_type: str, event_class: type[T], event_handler: IEventHandler[T]) -> None:
        # Route business handlers through the repo (not directly to the broker).
        # Each subscribe call also wires the broker → repo_publisher for that event type.
        self.repo_subscriber._subscribe(event_type, event_class, event_handler)
        self.broker_subscriber._subscribe(event_type, event_class, self.repo_publisher)

    def cleanup(self) -> None:
        self.broker_subscriber.cleanup()
        self.repo_subscriber.cleanup()

    def tasks(self) -> list[Task]:
        return self.broker_subscriber.tasks() + self.repo_subscriber.tasks()


# features from: https://github.com/adimiko/TransactionalBox
class TransactionalBox(IEventBus[IntegrationEvent], IBackgroundTasks):
    def __init__(self, inbox: InboxEventSubscriber[Any, Any], outbox: OutboxEventPublisher[Any]):
        self.inbox = inbox
        self.outbox = outbox

    def publish(self, events: Iterable[IntegrationEvent]) -> None:
        self.outbox.publish(events)

    def _subscribe(self, event_type: str, event_class: type[IntegrationEvent], event_handler: IEventHandler[IntegrationEvent]) -> None:
        self.inbox.subscribe(event_type, event_handler)

    def cleanup(self) -> None:
        self.inbox.cleanup()
        self.outbox.cleanup()

    def tasks(self) -> list[Task]:
        return self.inbox.tasks() + self.outbox.tasks()
