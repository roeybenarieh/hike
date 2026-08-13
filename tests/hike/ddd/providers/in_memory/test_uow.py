"""Integration tests for UnitOfWork + InMemoryRepository."""
from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from hike.ddd.providers.in_memory import InMemoryDBContext, InMemoryRepository
from hike.ddd.repository import (
    AggregateAlreadyExistError,
    AggregateDoesNotExistError,
    OptimisticLockError,
    get_version,
)
from hike.ddd.uow import UnitOfWork

from tests.hike.ddd.conftest import Boat, Checkpoint, Journey, Name, Price


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_journey_uow() -> UnitOfWork[dict[Any, Any], UUID, Journey]:
    context = InMemoryDBContext()
    repo: InMemoryRepository[UUID, Journey] = InMemoryRepository()
    return UnitOfWork(context, repo=repo)


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


def test_optimistic_lock_conflict() -> None:
    """Second writer loses when it holds a stale version."""
    boat = Boat(name=Name("Contested"), price=Price(100.0))
    uow = make_uow()

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        copy_a = uow.repo.get_one(boat.id)
    with uow:
        copy_b = uow.repo.get_one(boat.id)

    assert get_version(copy_a) == 0
    assert get_version(copy_b) == 0

    copy_a.price = Price(200.0)
    with uow:
        uow.repo.update(copy_a)
        uow.commit()
    assert get_version(copy_a) == 1

    copy_b.price = Price(300.0)
    with pytest.raises(OptimisticLockError):
        with uow:
            uow.repo.update(copy_b)
            uow.commit()


def test_journey_save_and_get_one_with_checkpoints() -> None:
    uow = make_journey_uow()
    cp1 = Checkpoint(name=Name("Paris"))
    cp2 = Checkpoint(name=Name("Lyon"))
    journey = Journey(name=Name("France Trip"), checkpoints=[cp1, cp2])

    with uow:
        uow.repo.save(journey)
        uow.commit()

    with uow:
        fetched = uow.repo.get_one(journey.id)

    assert fetched.name == Name("France Trip")
    assert len(fetched.checkpoints) == 2
    assert {cp.name for cp in fetched.checkpoints} == {Name("Paris"), Name("Lyon")}


def test_journey_update_checkpoints() -> None:
    uow = make_journey_uow()
    journey = Journey(name=Name("Tour"), checkpoints=[Checkpoint(name=Name("A"))])

    with uow:
        uow.repo.save(journey)
        uow.commit()

    journey.checkpoints.append(Checkpoint(name=Name("B")))
    with uow:
        uow.repo.update(journey)
        uow.commit()

    with uow:
        fetched = uow.repo.get_one(journey.id)

    assert len(fetched.checkpoints) == 2
