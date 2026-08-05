"""Integration tests for UnitOfWork + InMemoryRepository."""
from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from hike.ddd.providers.in_memory import InMemoryDBContext, InMemoryRepository
from hike.ddd.repository import (
    AggregateDoesNotExistError,
    AggregateAlreadyExistError,
)
from hike.ddd.uow import UnitOfWork

from tests.hike.ddd.conftest import Boat, Name, Price


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_uow() -> UnitOfWork[dict[Any, Any], UUID, Boat]:
    context = InMemoryDBContext()
    repo: InMemoryRepository[UUID, Boat] = InMemoryRepository()
    return UnitOfWork(context, repo=repo)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_save_and_get_one() -> None:
    boat = Boat(name=Name("Sea Spirit"), price=Price(4_999.99))
    uow = make_uow()

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        fetched = uow.repo.get_one(boat.id)

    assert fetched.name == Name("Sea Spirit")
    assert fetched.price == Price(4_999.99)


def test_update() -> None:
    boat = Boat(name=Name("Old Name"), price=Price(100.0))
    uow = make_uow()

    with uow:
        uow.repo.save(boat)
        uow.commit()

    boat.price = Price(200.0)
    with uow:
        uow.repo.update(boat)
        uow.commit()

    with uow:
        fetched = uow.repo.get_one(boat.id)

    assert fetched.price == Price(200.0)


def test_get_many_with_spec() -> None:
    boat_a = Boat(name=Name("Alpha"), price=Price(10.0))
    boat_b = Boat(name=Name("Beta"), price=Price(50.0))
    uow = make_uow()

    with uow:
        uow.repo.save(boat_a)
        uow.repo.save(boat_b)
        uow.commit()

    with uow:
        results = uow.repo.get_many(Boat.price > 20.0)

    assert len(results) == 1
    assert results[0].name == Name("Beta")


def test_upsert_creates_then_updates() -> None:
    boat = Boat(name=Name("Ghost"), price=Price(1.0))
    uow = make_uow()

    with uow:
        uow.repo.upsert(boat)  # create
        uow.commit()

    boat.price = Price(2.0)
    with uow:
        uow.repo.upsert(boat)  # update
        uow.commit()

    with uow:
        # FIX: something
        fetched = uow.repo.get_one(boat.id)

    assert fetched.price == Price(2.0)


def test_delete() -> None:
    boat = Boat(name=Name("Doomed"), price=Price(0.01))
    uow = make_uow()

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        uow.repo.delete(boat)
        uow.commit()

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(boat.id)


def test_save_duplicate_raises() -> None:
    boat = Boat(name=Name("Twin"), price=Price(50.0))
    uow = make_uow()

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with pytest.raises(AggregateAlreadyExistError):
        with uow:
            uow.repo.save(boat)
            uow.commit()


def test_rollback_on_exception() -> None:
    """Exception inside the UoW block must abort the transaction."""
    boat = Boat(name=Name("Rollback Boat"), price=Price(99.0))
    uow = make_uow()

    with pytest.raises(ValueError, match="simulated failure"):
        with uow:
            uow.repo.save(boat)
            raise ValueError("simulated failure")

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(boat.id)
