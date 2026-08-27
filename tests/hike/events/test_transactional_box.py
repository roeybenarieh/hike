"""Tests for OutboxEventPublisher, InboxEventSubscriber, and TransactionalBox."""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

from hike.events.integration_event import IntegrationEvent
from hike.events.providers.repository import RepositoryEventPublisher, RepositoryEventSubscriber
from hike.events.interfaces import IEventHandler
from hike.events.transactional_box import InboxEventSubscriber, OutboxEventPublisher, TransactionalBox


@dataclass(frozen=True, kw_only=True)
class _Ev(IntegrationEvent):
    payload: str
    version: int = 1


class TestOutboxEventPublisher:
    def test_init_subscribes_broker_publisher_on_repo_subscriber(self) -> None:
        repo_pub: MagicMock = MagicMock(spec=RepositoryEventPublisher)
        repo_sub: MagicMock = MagicMock(spec=RepositoryEventSubscriber)
        broker_pub: MagicMock = MagicMock()
        OutboxEventPublisher(repo_pub, repo_sub, broker_pub)  # type: ignore[arg-type]
        repo_sub.subscribe.assert_called_once_with(broker_pub)

    def test_publish_delegates_to_repo_publisher(self) -> None:
        repo_pub: MagicMock = MagicMock(spec=RepositoryEventPublisher)
        repo_sub: MagicMock = MagicMock(spec=RepositoryEventSubscriber)
        outbox = OutboxEventPublisher(repo_pub, repo_sub, MagicMock())  # type: ignore[arg-type]
        events = [_Ev(payload="x")]
        outbox.publish(events)  # pyright: ignore[reportUnknownMemberType]
        repo_pub.publish.assert_called_once_with(events)

    def test_cleanup_delegates_to_repo_subscriber(self) -> None:
        repo_pub: MagicMock = MagicMock(spec=RepositoryEventPublisher)
        repo_sub: MagicMock = MagicMock(spec=RepositoryEventSubscriber)
        outbox = OutboxEventPublisher(repo_pub, repo_sub, MagicMock())  # type: ignore[arg-type]
        outbox.cleanup()
        repo_sub.cleanup.assert_called_once()

    def test_tasks_returns_repo_subscriber_start(self) -> None:
        repo_pub: MagicMock = MagicMock(spec=RepositoryEventPublisher)
        repo_sub: MagicMock = MagicMock(spec=RepositoryEventSubscriber)
        outbox = OutboxEventPublisher(repo_pub, repo_sub, MagicMock())  # type: ignore[arg-type]
        assert outbox.tasks() == [repo_sub.start]


class TestInboxEventSubscriber:
    def test_cleanup_calls_broker_and_repo_subscribers(self) -> None:
        broker_sub: MagicMock = MagicMock()
        repo_pub: MagicMock = MagicMock(spec=RepositoryEventPublisher)
        repo_sub: MagicMock = MagicMock(spec=RepositoryEventSubscriber)
        inbox = InboxEventSubscriber(broker_sub, repo_pub, repo_sub)  # type: ignore[arg-type]
        inbox.cleanup()
        broker_sub.cleanup.assert_called_once()
        repo_sub.cleanup.assert_called_once()

    def test_tasks_combines_broker_and_repo_subscriber_tasks(self) -> None:
        broker_sub: MagicMock = MagicMock()
        repo_pub: MagicMock = MagicMock(spec=RepositoryEventPublisher)
        repo_sub: MagicMock = MagicMock(spec=RepositoryEventSubscriber)
        inbox = InboxEventSubscriber(broker_sub, repo_pub, repo_sub)  # type: ignore[arg-type]
        t1, t2 = MagicMock(), MagicMock()
        broker_sub.tasks.return_value = [t1]
        repo_sub.tasks.return_value = [t2]
        assert inbox.tasks() == [t1, t2]


class TestTransactionalBox:
    def test_publish_delegates_to_outbox(self) -> None:
        inbox: MagicMock = MagicMock(spec=InboxEventSubscriber)
        outbox: MagicMock = MagicMock(spec=OutboxEventPublisher)
        box = TransactionalBox(inbox, outbox)  # type: ignore[arg-type]
        events = [_Ev(payload="x")]
        box.publish(events)
        outbox.publish.assert_called_once_with(events)

    def test_subscribe_delegates_to_inbox(self) -> None:
        inbox: MagicMock = MagicMock(spec=InboxEventSubscriber)
        outbox: MagicMock = MagicMock(spec=OutboxEventPublisher)
        box = TransactionalBox(inbox, outbox)  # type: ignore[arg-type]

        class _H(IEventHandler[_Ev]):
            def handle(self, event: _Ev) -> None: ...

        handler = _H()
        box._subscribe("_Ev", _Ev, handler)  # pyright: ignore[reportPrivateUsage,reportUnknownMemberType,reportArgumentType]
        inbox.subscribe.assert_called_once_with("_Ev", handler)

    def test_cleanup_calls_inbox_and_outbox(self) -> None:
        inbox: MagicMock = MagicMock(spec=InboxEventSubscriber)
        outbox: MagicMock = MagicMock(spec=OutboxEventPublisher)
        box = TransactionalBox(inbox, outbox)  # type: ignore[arg-type]
        box.cleanup()
        inbox.cleanup.assert_called_once()
        outbox.cleanup.assert_called_once()

    def test_tasks_merges_inbox_and_outbox_tasks(self) -> None:
        inbox: MagicMock = MagicMock(spec=InboxEventSubscriber)
        outbox: MagicMock = MagicMock(spec=OutboxEventPublisher)
        box = TransactionalBox(inbox, outbox)  # type: ignore[arg-type]
        t1, t2 = MagicMock(), MagicMock()
        inbox.tasks.return_value = [t1]
        outbox.tasks.return_value = [t2]
        assert box.tasks() == [t1, t2]
