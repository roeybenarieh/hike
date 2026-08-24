"""Tests for DomainEvent persistence via InMemoryPersistableRepository."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import pytest

from hike.domain_event import DomainEvent
from hike.persistence.persistable import Persistable
from hike.persistence.providers.in_memory import InMemoryPersistableRepository
from hike.persistence.repository import (
    ResourceAlreadyExistError,
    ResourceDoesNotExistError,
    OptimisticLockError,
)


@dataclass(frozen=True, kw_only=True, eq=False)
class OrderShipped(DomainEvent):
    order_id: str


class TestDomainEventIdAutoGeneration:
    def test_unique_id_per_instance(self) -> None:
        e1 = OrderShipped(order_id="A")
        e2 = OrderShipped(order_id="A")
        assert e1.id != e2.id

    def test_id_is_uuid(self) -> None:
        event = OrderShipped(order_id="A")
        assert isinstance(event.id, UUID)

    def test_explicit_id_is_accepted(self) -> None:
        fixed = UUID("12345678-1234-5678-1234-567812345678")
        event = OrderShipped(order_id="A", id=fixed)
        assert event.id == fixed


class TestDomainEventPersistable:
    def test_is_persistable(self) -> None:
        assert isinstance(OrderShipped(order_id="A"), Persistable)

    def test_equality_by_id(self) -> None:
        fixed_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        e1 = OrderShipped(order_id="X", id=fixed_id)
        e2 = OrderShipped(order_id="Y", id=fixed_id)
        assert e1 == e2

    def test_inequality_different_id(self) -> None:
        e1 = OrderShipped(order_id="X")
        e2 = OrderShipped(order_id="X")
        assert e1 != e2

    def test_hashable_by_id(self) -> None:
        fixed_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        e1 = OrderShipped(order_id="X", id=fixed_id)
        e2 = OrderShipped(order_id="Y", id=fixed_id)
        assert hash(e1) == hash(e2)
        assert len({e1, e2}) == 1


class TestInMemoryPersistableRepositoryWithDomainEvent:
    @pytest.fixture
    def repo(self) -> InMemoryPersistableRepository[UUID, OrderShipped]:
        return InMemoryPersistableRepository()

    def test_save_and_get_one_round_trip(
        self, repo: InMemoryPersistableRepository[UUID, OrderShipped]
    ) -> None:
        event = OrderShipped(order_id="123")
        repo.session = {}
        returned_id = repo.save(event)
        assert returned_id == event.id

        retrieved = repo.get_one(event.id)
        assert retrieved.order_id == event.order_id
        assert retrieved.id == event.id

    def test_save_duplicate_raises(
        self, repo: InMemoryPersistableRepository[UUID, OrderShipped]
    ) -> None:
        event = OrderShipped(order_id="dup")
        repo.session = {}
        repo.save(event)
        with pytest.raises(ResourceAlreadyExistError):
            repo.save(event)

    def test_delete_by_object(
        self, repo: InMemoryPersistableRepository[UUID, OrderShipped]
    ) -> None:
        event = OrderShipped(order_id="del")
        repo.session = {}
        repo.save(event)
        repo.delete(event)
        with pytest.raises(ResourceDoesNotExistError):
            repo.get_one(event.id)

    def test_delete_by_id(
        self, repo: InMemoryPersistableRepository[UUID, OrderShipped]
    ) -> None:
        event = OrderShipped(order_id="del-id")
        repo.session = {}
        repo.save(event)
        repo.delete(event.id)
        with pytest.raises(ResourceDoesNotExistError):
            repo.get_one(event.id)

    def test_delete_nonexistent_raises(
        self, repo: InMemoryPersistableRepository[UUID, OrderShipped]
    ) -> None:
        event = OrderShipped(order_id="ghost")
        repo.session = {}
        with pytest.raises(ResourceDoesNotExistError):
            repo.delete(event.id)

    def test_update_round_trip(
        self, repo: InMemoryPersistableRepository[UUID, OrderShipped]
    ) -> None:
        event = OrderShipped(order_id="before")
        repo.session = {}
        repo.save(event)
        assert event.get_version() == 0

        updated = OrderShipped(order_id="after", id=event.id)
        # version must match stored version (0)
        updated.set_version(0)
        repo.update(updated)
        assert updated.get_version() == 1

        retrieved = repo.get_one(event.id)
        assert retrieved.order_id == "after"

    def test_update_optimistic_lock(
        self, repo: InMemoryPersistableRepository[UUID, OrderShipped]
    ) -> None:
        event = OrderShipped(order_id="v0")
        repo.session = {}
        repo.save(event)

        stale = OrderShipped(order_id="stale", id=event.id)
        # stale version 99 does not match stored 0
        stale.set_version(99)
        with pytest.raises(OptimisticLockError):
            repo.update(stale)


