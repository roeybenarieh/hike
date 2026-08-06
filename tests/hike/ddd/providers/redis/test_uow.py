"""Integration tests for UnitOfWork + RedisRepository against a real Redis
instance managed by testcontainers.

Run with::

    uv run pytest tests/hike/ddd/providers/redis/test_uow.py -v
"""
from __future__ import annotations

from uuid import UUID

import pytest
from redis import Redis
from redis.client import Pipeline
from testcontainers.community.redis import RedisContainer  # pyright: ignore[reportMissingImports]

from hike.ddd.entity import EntityID
from hike.ddd.providers.redis import RedisDBContext, RedisRepository
from hike.ddd.repository import AggregateAlreadyExistError, AggregateDoesNotExistError, OptimisticLockError
from hike.ddd.uow import UnitOfWork

from tests.hike.ddd.conftest import Boat, Checkpoint, Journey, Name, Price


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

KEY_PREFIX = "boats"
JOURNEY_KEY_PREFIX = "journeys"


@pytest.fixture(scope="session")
def redis_client() -> Redis:  # type: ignore[misc]
    with RedisContainer("redis:7") as container:
        yield container.get_client()  # type: ignore[misc]


@pytest.fixture(autouse=True)
def flush_redis(redis_client: Redis) -> None:  # type: ignore[misc]
    yield  # type: ignore[misc]
    redis_client.flushdb()  # pyright: ignore[reportUnknownMemberType]


@pytest.fixture
def uow(redis_client: Redis) -> UnitOfWork[Pipeline, UUID, Boat]:
    ctx = RedisDBContext(redis_client)
    repo: RedisRepository[UUID, Boat] = RedisRepository(redis_client, Boat, KEY_PREFIX)
    return UnitOfWork(ctx, repo=repo)


@pytest.fixture
def journey_uow(redis_client: Redis) -> UnitOfWork[Pipeline, UUID, Journey]:
    ctx = RedisDBContext(redis_client)
    repo: RedisRepository[UUID, Journey] = RedisRepository(redis_client, Journey, JOURNEY_KEY_PREFIX)
    return UnitOfWork(ctx, repo=repo)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_save_and_get_one(uow: UnitOfWork[Pipeline, UUID, Boat]) -> None:
    boat = Boat(name=Name("Sea Spirit"), price=Price(4_999.99))

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        fetched = uow.repo.get_one(boat.id)

    assert fetched.name == Name("Sea Spirit")
    assert fetched.price == Price(4_999.99)


def test_update(uow: UnitOfWork[Pipeline, UUID, Boat]) -> None:
    boat = Boat(name=Name("Old Name"), price=Price(100.0))

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


def test_get_many_with_spec(uow: UnitOfWork[Pipeline, UUID, Boat]) -> None:
    boat_a = Boat(name=Name("Alpha"), price=Price(10.0))
    boat_b = Boat(name=Name("Beta"), price=Price(50.0))

    with uow:
        uow.repo.save(boat_a)
        uow.repo.save(boat_b)
        uow.commit()

    with uow:
        results = uow.repo.get_many(Boat.price > 20.0)

    assert len(results) == 1
    assert results[0].name == Name("Beta")


def test_upsert_creates_then_updates(uow: UnitOfWork[Pipeline, UUID, Boat]) -> None:
    boat = Boat(name=Name("Ghost"), price=Price(1.0))

    with uow:
        uow.repo.upsert(boat)
        uow.commit()

    boat.price = Price(2.0)
    with uow:
        uow.repo.upsert(boat)
        uow.commit()

    with uow:
        fetched = uow.repo.get_one(boat.id)

    assert fetched.price == Price(2.0)


def test_delete(uow: UnitOfWork[Pipeline, UUID, Boat]) -> None:
    boat = Boat(name=Name("Doomed"), price=Price(0.01))

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        uow.repo.delete(boat)
        uow.commit()

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(boat.id)


def test_save_duplicate_raises(uow: UnitOfWork[Pipeline, UUID, Boat]) -> None:
    boat = Boat(name=Name("Twin"), price=Price(50.0))

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with pytest.raises(AggregateAlreadyExistError):
        with uow:
            uow.repo.save(boat)
            uow.commit()


def test_get_one_missing_raises(uow: UnitOfWork[Pipeline, UUID, Boat]) -> None:
    from uuid import uuid4

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(EntityID(uuid4()))


def test_delete_missing_raises(uow: UnitOfWork[Pipeline, UUID, Boat]) -> None:
    ghost = Boat(name=Name("Never Saved"), price=Price(1.0))

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.delete(ghost)


def test_rollback_on_exception(uow: UnitOfWork[Pipeline, UUID, Boat]) -> None:
    """Write queued in pipeline must be discarded when the UoW block raises."""
    boat = Boat(name=Name("Rollback Boat"), price=Price(99.0))

    with pytest.raises(ValueError, match="simulated failure"):
        with uow:
            uow.repo.save(boat)
            raise ValueError("simulated failure")

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(boat.id)


def test_optimistic_lock_conflict(uow: UnitOfWork[Pipeline, UUID, Boat]) -> None:
    """Second writer loses when it holds a stale version."""
    boat = Boat(name=Name("Contested"), price=Price(100.0))

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        copy_a = uow.repo.get_one(boat.id)
    with uow:
        copy_b = uow.repo.get_one(boat.id)

    assert copy_a.version == 0
    assert copy_b.version == 0

    copy_a.price = Price(200.0)
    with uow:
        uow.repo.update(copy_a)
        uow.commit()
    assert copy_a.version == 1

    copy_b.price = Price(300.0)
    with pytest.raises(OptimisticLockError):
        with uow:
            uow.repo.update(copy_b)
            uow.commit()


def test_journey_save_and_get_one_with_checkpoints(journey_uow: UnitOfWork[Pipeline, UUID, Journey]) -> None:
    cp1 = Checkpoint(name=Name("Paris"))
    cp2 = Checkpoint(name=Name("Lyon"))
    journey = Journey(name=Name("France Trip"), checkpoints=[cp1, cp2])

    with journey_uow:
        journey_uow.repo.save(journey)
        journey_uow.commit()

    with journey_uow:
        fetched = journey_uow.repo.get_one(journey.id)

    assert fetched.name == Name("France Trip")
    assert len(fetched.checkpoints) == 2
    assert {cp.name for cp in fetched.checkpoints} == {Name("Paris"), Name("Lyon")}


def test_journey_update_checkpoints(journey_uow: UnitOfWork[Pipeline, UUID, Journey]) -> None:
    journey = Journey(name=Name("Tour"), checkpoints=[Checkpoint(name=Name("A"))])

    with journey_uow:
        journey_uow.repo.save(journey)
        journey_uow.commit()

    journey.checkpoints.append(Checkpoint(name=Name("B")))
    with journey_uow:
        journey_uow.repo.update(journey)
        journey_uow.commit()

    with journey_uow:
        fetched = journey_uow.repo.get_one(journey.id)

    assert len(fetched.checkpoints) == 2
