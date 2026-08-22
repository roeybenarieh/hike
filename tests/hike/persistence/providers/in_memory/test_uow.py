"""Integration tests for UnitOfWork + InMemoryRepository."""
from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from hike.persistence.providers.in_memory import InMemoryDBContext, InMemoryRepository
from hike.persistence.uow import UnitOfWork

from tests.hike.conftest import Boat, Journey
from tests.hike.persistence.providers.parity_suite import RepositoryParitySuite


class TestInMemoryRepositoryParity(RepositoryParitySuite):
    @pytest.fixture
    def uow(self) -> UnitOfWork[Any]:
        return UnitOfWork(InMemoryDBContext())

    @pytest.fixture
    def repo(self) -> InMemoryRepository[UUID, Boat]:
        return InMemoryRepository()

    @pytest.fixture
    def journey_repo(self) -> InMemoryRepository[UUID, Journey]:
        return InMemoryRepository()
