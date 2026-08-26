"""Tests for TransactionalBoxBuilder."""
from __future__ import annotations

from typing import Any, Iterable, NoReturn

import pytest

from hike.domain_event import DomainEvent
from hike.events.builder import TransactionalBoxBuilder
from hike.events.interfaces import IEventHandler, IEventPublisher
from hike.events.interfaces.background_task import Task
from hike.events.interfaces.subscriber import IExternalEventSubscriber
from hike.events.transactional_box import TransactionalBox
from hike.persistence.persistable import Persistable
from hike.persistence.providers.in_memory import InMemoryDBContext, InMemoryPersistableRepository
from hike.persistence.uow import UnitOfWork


class _Publisher(IEventPublisher[DomainEvent]):
    def publish(self, events: Iterable[DomainEvent]) -> None:
        pass


class _Subscriber(IExternalEventSubscriber[DomainEvent]):
    def _subscribe(  # type: ignore[override]
        self, event_type: str, event_class: type, event_handler: IEventHandler  # type: ignore[type-arg]
    ) -> None:
        super()._subscribe(event_type, event_class, event_handler)  # type: ignore[arg-type]

    def cleanup(self) -> None:
        pass

    def start(self) -> NoReturn:
        raise NotImplementedError

    def tasks(self) -> list[Task]:
        return []


def _repo() -> InMemoryPersistableRepository[Any, Any]:
    return InMemoryPersistableRepository()


def _uow() -> UnitOfWork[dict[Any, Persistable[Any]]]:
    return UnitOfWork(InMemoryDBContext())


class TestTransactionalBoxBuilder:
    def test_build_raises_when_inbox_not_configured(self) -> None:
        builder = (
            TransactionalBoxBuilder()
            .outbox_repository(_repo(), _uow())
            .broker_publisher(_Publisher())
            .broker_subscriber(_Subscriber())  # pyright: ignore[reportAbstractUsage]
        )
        with pytest.raises(ValueError, match="[Ii]nbox"):
            builder.build()

    def test_build_raises_when_outbox_not_configured(self) -> None:
        builder = (
            TransactionalBoxBuilder()
            .inbox_repository(_repo(), _uow())
            .broker_publisher(_Publisher())
            .broker_subscriber(_Subscriber())  # pyright: ignore[reportAbstractUsage]
        )
        with pytest.raises(ValueError, match="[Oo]utbox"):
            builder.build()

    def test_build_raises_when_broker_publisher_not_configured(self) -> None:
        builder = (
            TransactionalBoxBuilder()
            .inbox_repository(_repo(), _uow())
            .outbox_repository(_repo(), _uow())
            .broker_subscriber(_Subscriber())  # pyright: ignore[reportAbstractUsage]
        )
        with pytest.raises(ValueError, match="[Bb]roker publisher"):
            builder.build()

    def test_build_raises_when_broker_subscriber_not_configured(self) -> None:
        builder = (
            TransactionalBoxBuilder()
            .inbox_repository(_repo(), _uow())
            .outbox_repository(_repo(), _uow())
            .broker_publisher(_Publisher())
        )
        with pytest.raises(ValueError, match="[Bb]roker subscriber"):
            builder.build()

    def test_build_returns_transactional_box(self) -> None:
        box = (
            TransactionalBoxBuilder()
            .inbox_repository(_repo(), _uow())
            .outbox_repository(_repo(), _uow())
            .broker_publisher(_Publisher())
            .broker_subscriber(_Subscriber())  # pyright: ignore[reportAbstractUsage]
            .build()
        )
        assert isinstance(box, TransactionalBox)

    def test_builder_methods_return_self_for_chaining(self) -> None:
        builder = TransactionalBoxBuilder()
        assert builder.inbox_repository(_repo(), _uow()) is builder
        assert builder.outbox_repository(_repo(), _uow()) is builder
        assert builder.broker_publisher(_Publisher()) is builder
        assert builder.broker_subscriber(_Subscriber()) is builder  # pyright: ignore[reportAbstractUsage]
