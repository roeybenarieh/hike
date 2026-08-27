"""Tests for RepositoryEventPublisher and RepositoryEventSubscriber."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import pytest

from hike.events.integration_event import IntegrationEvent
from hike.events.providers.repository import RepositoryEventPublisher, RepositoryEventSubscriber
from hike.events.interfaces import IEventHandler
from hike.persistence.persistable import Persistable
from hike.persistence.providers.in_memory import InMemoryDBContext, InMemoryPersistableRepository
from hike.persistence.repository import ResourceDoesNotExistError
from hike.persistence.uow import UnitOfWork


@dataclass(frozen=True, kw_only=True)
class _Ev(IntegrationEvent):
    payload: str
    version: int = 1


@pytest.fixture
def repo() -> InMemoryPersistableRepository[Any, _Ev]:
    return InMemoryPersistableRepository()


@pytest.fixture
def ctx() -> InMemoryDBContext:
    return InMemoryDBContext()


@pytest.fixture
def uow(ctx: InMemoryDBContext) -> UnitOfWork[dict[Any, Persistable[Any]]]:
    return UnitOfWork(ctx)


class TestRepositoryEventPublisher:
    def test_publish_saves_event_to_repo(
        self,
        repo: InMemoryPersistableRepository[Any, _Ev],
        uow: UnitOfWork[dict[Any, Persistable[Any]]],
    ) -> None:
        publisher: RepositoryEventPublisher[_Ev, dict[Any, Persistable[Any]]] = RepositoryEventPublisher(repo)
        event = _Ev(payload="test")
        with uow(repo, auto_commit=True):
            publisher.publish([event])
        with uow(repo):
            retrieved = repo.get_one(event.id)
        assert retrieved.payload == "test"

    def test_publish_saves_multiple_events(
        self,
        repo: InMemoryPersistableRepository[Any, _Ev],
        uow: UnitOfWork[dict[Any, Persistable[Any]]],
    ) -> None:
        publisher: RepositoryEventPublisher[_Ev, dict[Any, Persistable[Any]]] = RepositoryEventPublisher(repo)
        events = [_Ev(payload=f"e{i}") for i in range(3)]
        with uow(repo, auto_commit=True):
            publisher.publish(events)
        with uow(repo):
            for ev in events:
                assert repo.get_one(ev.id).payload == ev.payload

    def test_publish_empty_iterable_is_noop(
        self,
        repo: InMemoryPersistableRepository[Any, _Ev],
        uow: UnitOfWork[dict[Any, Persistable[Any]]],
    ) -> None:
        publisher: RepositoryEventPublisher[_Ev, dict[Any, Persistable[Any]]] = RepositoryEventPublisher(repo)
        with uow(repo, auto_commit=True):
            publisher.publish([])  # must not raise


class TestRepositoryEventSubscriber:
    def test_tasks_returns_start_callable(
        self,
        repo: InMemoryPersistableRepository[Any, _Ev],
        uow: UnitOfWork[dict[Any, Persistable[Any]]],
    ) -> None:
        subscriber: RepositoryEventSubscriber[_Ev, dict[Any, Persistable[Any]]] = RepositoryEventSubscriber(repo, uow)  # pyright: ignore[reportAbstractUsage]
        assert subscriber.tasks() == [subscriber.start]

    def test_start_dispatches_event_to_handler(
        self,
        repo: InMemoryPersistableRepository[Any, _Ev],
        uow: UnitOfWork[dict[Any, Persistable[Any]]],
    ) -> None:
        subscriber: RepositoryEventSubscriber[_Ev, dict[Any, Persistable[Any]]] = RepositoryEventSubscriber(repo, uow)  # pyright: ignore[reportAbstractUsage]

        received: list[_Ev] = []
        done = threading.Event()

        class _H(IEventHandler[_Ev]):
            def handle(self, event: _Ev) -> None:
                received.append(event)
                done.set()

        subscriber.subscribe(_H())  # type: ignore[arg-type]

        t = threading.Thread(target=subscriber.start, daemon=True)
        t.start()
        time.sleep(0.05)  # let subscriber enter watch()

        event = _Ev(payload="hello")
        with uow(repo, auto_commit=True):
            repo.save(event)

        assert done.wait(timeout=5), "handler was not called within 5 s"
        assert received[0].payload == "hello"

    def test_start_deletes_event_after_handling(
        self,
        repo: InMemoryPersistableRepository[Any, _Ev],
        uow: UnitOfWork[dict[Any, Persistable[Any]]],
    ) -> None:
        subscriber: RepositoryEventSubscriber[_Ev, dict[Any, Persistable[Any]]] = RepositoryEventSubscriber(repo, uow)  # pyright: ignore[reportAbstractUsage]

        done = threading.Event()

        class _H(IEventHandler[_Ev]):
            def handle(self, event: _Ev) -> None:
                done.set()

        subscriber.subscribe(_H())  # type: ignore[arg-type]

        t = threading.Thread(target=subscriber.start, daemon=True)
        t.start()
        time.sleep(0.05)

        event = _Ev(payload="bye")
        with uow(repo, auto_commit=True):
            repo.save(event)

        assert done.wait(timeout=5)
        time.sleep(0.1)  # allow delete + commit to complete

        with uow(repo):
            with pytest.raises(ResourceDoesNotExistError):
                repo.get_one(event.id)
