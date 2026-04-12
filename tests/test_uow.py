"""Tests for UnitOfWork + InMemoryRepository."""
from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest

from cliff.ddd.aggregate import UuidAggregate
from cliff.ddd.uow import InMemoryDBContext
from cliff.ddd.entity import Field
from cliff.ddd.repository import (
    AggregateDoesNotExistError,
    AggregateAlreadyExistError,
    InMemoryRepository,
)
from cliff.ddd.uow import UnitOfWork
from cliff.ddd.value_object import ValueObject


class Price(ValueObject[float]):
    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("Price cannot be negative")


class Boat(UuidAggregate):
    name: str
    price: Field[Price]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_uow() -> UnitOfWork[dict, UUID]:
    context = InMemoryDBContext()
    repo: InMemoryRepository[UUID] = InMemoryRepository()
    return UnitOfWork(context, repo=repo)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_save_and_get_one() -> None:
    boat = Boat(name="Sea Spirit", price=Price(4_999.99))
    uow = make_uow()

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        fetched = cast(Boat, uow.repo.get_one(boat.id.value))

    assert fetched.name == "Sea Spirit"
    assert fetched.price == Price(4_999.99)


def test_update() -> None:
    boat = Boat(name="Old Name", price=Price(100.0))
    uow = make_uow()

    with uow:
        uow.repo.save(boat)
        uow.commit()

    boat.price = Price(200.0)
    with uow:
        uow.repo.update(boat)
        uow.commit()

    with uow:
        fetched = cast(Boat, uow.repo.get_one(boat.id.value))

    assert fetched.price == Price(200.0)


def test_get_many_with_spec() -> None:
    boat_a = Boat(name="Alpha", price=Price(10.0))
    boat_b = Boat(name="Beta", price=Price(50.0))
    uow = make_uow()

    with uow:
        uow.repo.save(boat_a)
        uow.repo.save(boat_b)
        uow.commit()

    with uow:
        results = uow.repo.get_many(Boat.price > 20.0)

    assert len(results) == 1
    assert cast(Boat, results[0]).name == "Beta"


def test_upsert_creates_then_updates() -> None:
    boat = Boat(name="Ghost", price=Price(1.0))
    uow = make_uow()

    with uow:
        uow.repo.upsert(boat)  # create
        uow.commit()

    boat.price = Price(2.0)
    with uow:
        uow.repo.upsert(boat)  # update
        uow.commit()

    with uow:
        fetched = cast(Boat, uow.repo.get_one(boat.id.value))

    assert fetched.price == Price(2.0)


def test_delete() -> None:
    boat = Boat(name="Doomed", price=Price(0.01))
    uow = make_uow()

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        uow.repo.delete(boat)
        uow.commit()

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(boat.id.value)


def test_save_duplicate_raises() -> None:
    boat = Boat(name="Twin", price=Price(50.0))
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
    boat = Boat(name="Rollback Boat", price=Price(99.0))
    uow = make_uow()

    with pytest.raises(ValueError, match="simulated failure"):
        with uow:
            uow.repo.save(boat)
            raise ValueError("simulated failure")

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(boat.id.value)
