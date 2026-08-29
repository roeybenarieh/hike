"""Integration tests for UnitOfWork + InMemoryRepository."""
from __future__ import annotations

import threading
import time
from typing import Any
from uuid import UUID, uuid4

import pytest

from hike.persistence.providers.in_memory import InMemoryDBContext, InMemoryRepository
from hike.persistence.uow import UnitOfWork

from tests.hike.conftest import Boat, Journey
from tests.hike.persistence.providers.parity_suite import LockParitySuite, RepositoryParitySuite


class TestInMemoryRepositoryParity(RepositoryParitySuite, LockParitySuite):
    @pytest.fixture
    def uow(self) -> UnitOfWork[Any]:
        return UnitOfWork(InMemoryDBContext())

    @pytest.fixture
    def repo(self) -> InMemoryRepository[UUID, Boat]:
        return InMemoryRepository[UUID, Boat]()

    @pytest.fixture
    def journey_repo(self) -> InMemoryRepository[UUID, Journey]:
        return InMemoryRepository[UUID, Journey]()

    @pytest.fixture
    def short_ttl_repo(self) -> InMemoryRepository[UUID, Boat]:
        return InMemoryRepository[UUID, Boat](lock_ttl=0.2)


class TestInMemoryConcurrentLock:
    """Concurrent locking test for InMemoryRepository."""

    def test_concurrent_lock_exclusion(self) -> None:
        """Two threads compete for the same lock; the second blocks until the first releases."""
        repo: InMemoryRepository[UUID, Boat] = InMemoryRepository[UUID, Boat]()
        repo.session = {}
        boat_id = uuid4()
        results: list[str] = []

        def thread_a() -> None:
            repo.acquire_lock(boat_id, owner="owner-a")
            results.append("a-acquired")
            time.sleep(0.1)
            results.append("a-releasing")
            repo.release_lock(boat_id, owner="owner-a")

        def thread_b() -> None:
            time.sleep(0.02)
            repo.acquire_lock(boat_id, owner="owner-b", timeout=5.0)
            results.append("b-acquired")
            repo.release_lock(boat_id, owner="owner-b")

        ta = threading.Thread(target=thread_a)
        tb = threading.Thread(target=thread_b)
        ta.start()
        tb.start()
        ta.join(timeout=5.0)
        tb.join(timeout=5.0)

        assert results == ["a-acquired", "a-releasing", "b-acquired"]
