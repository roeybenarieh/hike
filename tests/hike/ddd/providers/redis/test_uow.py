"""Integration tests for UnitOfWork + RedisRepository against a real Redis
instance managed by testcontainers.

Run with::

    uv run pytest tests/hike/ddd/providers/redis/test_uow.py -v
"""
from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest
from redis import Redis
from redis.client import Pipeline
from testcontainers.redis import RedisContainer  # pyright: ignore[reportMissingTypeStubs]

from hike.ddd.aggregate import UuidAggregate
from hike.ddd.entity import Field
from hike.ddd.providers.redis import RedisDBContext, RedisRepository
from hike.ddd.repository import AggregateAlreadyExistError, AggregateDoesNotExistError
from hike.ddd.uow import UnitOfWork
from hike.ddd.value_object import ValueObject


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


class Price(ValueObject[float]):
    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("Price cannot be negative")


class Boat(UuidAggregate):
    name: str
    price: Field[Price]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

KEY_PREFIX = "boats"


@pytest.fixture(scope="session")
def redis_client() -> Redis:  # type: ignore[misc]
    with RedisContainer("redis:7") as container:
        yield container.get_client()  # type: ignore[misc]


@pytest.fixture(autouse=True)
def flush_redis(redis_client: Redis) -> None:  # type: ignore[misc]
    yield  # type: ignore[misc]
    redis_client.flushdb()  # pyright: ignore[reportUnknownMemberType]


@pytest.fixture
def uow(redis_client: Redis) -> UnitOfWork[Pipeline, UUID]:
    ctx = RedisDBContext(redis_client)
    repo: RedisRepository[UUID] = RedisRepository(redis_client, Boat, KEY_PREFIX)
    return UnitOfWork(ctx, repo=repo)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_save_and_get_one(uow: UnitOfWork[Pipeline, UUID]) -> None:
    boat = Boat(name="Sea Spirit", price=Price(4_999.99))

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        fetched = cast(Boat, uow.repo.get_one(boat.id.value))

    assert fetched.name == "Sea Spirit"
    assert fetched.price == Price(4_999.99)


def test_update(uow: UnitOfWork[Pipeline, UUID]) -> None:
    boat = Boat(name="Old Name", price=Price(100.0))

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


def test_get_many_with_spec(uow: UnitOfWork[Pipeline, UUID]) -> None:
    boat_a = Boat(name="Alpha", price=Price(10.0))
    boat_b = Boat(name="Beta", price=Price(50.0))

    with uow:
        uow.repo.save(boat_a)
        uow.repo.save(boat_b)
        uow.commit()

    with uow:
        results = uow.repo.get_many(Boat.price > 20.0)

    assert len(results) == 1
    assert cast(Boat, results[0]).name == "Beta"


def test_upsert_creates_then_updates(uow: UnitOfWork[Pipeline, UUID]) -> None:
    boat = Boat(name="Ghost", price=Price(1.0))

    with uow:
        uow.repo.upsert(boat)
        uow.commit()

    boat.price = Price(2.0)
    with uow:
        uow.repo.upsert(boat)
        uow.commit()

    with uow:
        fetched = cast(Boat, uow.repo.get_one(boat.id.value))

    assert fetched.price == Price(2.0)


def test_delete(uow: UnitOfWork[Pipeline, UUID]) -> None:
    boat = Boat(name="Doomed", price=Price(0.01))

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        uow.repo.delete(boat)
        uow.commit()

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(boat.id.value)


def test_save_duplicate_raises(uow: UnitOfWork[Pipeline, UUID]) -> None:
    boat = Boat(name="Twin", price=Price(50.0))

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with pytest.raises(AggregateAlreadyExistError):
        with uow:
            uow.repo.save(boat)
            uow.commit()


def test_get_one_missing_raises(uow: UnitOfWork[Pipeline, UUID]) -> None:
    from uuid import uuid4

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(uuid4())


def test_delete_missing_raises(uow: UnitOfWork[Pipeline, UUID]) -> None:
    ghost = Boat(name="Never Saved", price=Price(1.0))

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.delete(ghost)


def test_rollback_on_exception(uow: UnitOfWork[Pipeline, UUID]) -> None:
    """Write queued in pipeline must be discarded when the UoW block raises."""
    boat = Boat(name="Rollback Boat", price=Price(99.0))

    with pytest.raises(ValueError, match="simulated failure"):
        with uow:
            uow.repo.save(boat)
            raise ValueError("simulated failure")

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(boat.id.value)


def test_locked_get_one(uow: UnitOfWork[Pipeline, UUID]) -> None:
    """locked=True acquires a distributed lock; release_locks() clears it."""
    boat = Boat(name="Locked", price=Price(10.0))

    with uow:
        uow.repo.save(boat)
        uow.commit()

    repo = cast(RedisRepository[UUID], uow.repo)
    with uow:
        fetched = cast(Boat, uow.repo.get_one(boat.id.value, locked=True))
        assert fetched.name == "Locked"
        assert repo.release_locks  # lock was acquired (will be released on next entry)

    # Entering the next UoW block assigns a new session, which calls release_locks().
    with uow:
        fetched2 = cast(Boat, uow.repo.get_one(boat.id.value))
        assert fetched2.name == "Locked"
