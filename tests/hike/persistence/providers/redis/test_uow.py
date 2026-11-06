"""Integration tests for UnitOfWork + RedisRepository against a real Redis
instance managed by testcontainers.

Run with::

    uv run pytest tests/hike/persistence/providers/redis/test_uow.py -v
"""
from __future__ import annotations

import multiprocessing
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from redis import Redis
from redis.client import Pipeline
from testcontainers.community.redis import RedisContainer  # pyright: ignore[reportMissingTypeStubs]

from hike.events.saga import SagaTimeout
from hike.persistence.providers.redis import RedisDBContext, RedisPersistableRepository, RedisRepository
from hike.persistence.uow import UnitOfWork

from tests.hike.conftest import Boat, Journey
from tests.hike.persistence.providers._subprocess_helpers import redis_insert_boat
from tests.hike.persistence.providers.parity_suite import CrossProcessWatchParitySuite, RepositoryParitySuite

KEY_PREFIX = "boats"
JOURNEY_KEY_PREFIX = "journeys"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def redis_client() -> Iterator[Redis]:  # type: ignore[type-arg]
    with RedisContainer("redis:7") as container:  # pyright: ignore[reportUnknownMemberType]
        client: Redis = container.get_client()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        yield client


@pytest.fixture(autouse=True)
def flush_redis(redis_client: Redis) -> None:  # type: ignore[misc]
    yield  # type: ignore[misc]
    redis_client.flushdb()  # pyright: ignore[reportUnknownMemberType]


# ---------------------------------------------------------------------------
# Parity tests
# ---------------------------------------------------------------------------


class TestRedisRepositoryParity(RepositoryParitySuite, CrossProcessWatchParitySuite):
    @pytest.fixture
    def uow(self, redis_client: Redis) -> UnitOfWork[Pipeline]:
        return UnitOfWork(RedisDBContext(redis_client))

    @pytest.fixture
    def repo(self, redis_client: Redis) -> RedisRepository[UUID, Boat]:
        return RedisRepository(redis_client, Boat, KEY_PREFIX)

    @pytest.fixture
    def journey_repo(self, redis_client: Redis) -> RedisRepository[UUID, Journey]:
        return RedisRepository(redis_client, Journey, JOURNEY_KEY_PREFIX)

    @pytest.fixture
    def cross_process_insert(self, redis_client: Redis) -> Callable[[Boat], None]:
        kwargs: Any = redis_client.connection_pool.connection_kwargs  # pyright: ignore[reportUnknownMemberType]
        host: str = kwargs.get("host", "localhost")
        port: int = kwargs.get("port", 6379)

        def _insert(boat: Boat) -> None:
            ctx = multiprocessing.get_context("spawn")
            p = ctx.Process(
                target=redis_insert_boat,
                args=(host, port, KEY_PREFIX,
                      str(boat.name.value), boat.price.value),
            )
            p.start()
            p.join(timeout=10)
            assert p.exitcode == 0, f"cross-process insert failed with exit code {p.exitcode}"

        return _insert


# ---------------------------------------------------------------------------
# Regression: datetime fields must survive Redis's JSON round-trip.
#
# _AggregateEncoder originally only knew how to encode uuid.UUID, so any
# Persistable with a raw datetime field (e.g. SagaTimeout.fire_at, produced
# by DataclassPersistable.to_dict()) raised TypeError on save() — a Redis-only
# gap invisible on PyMongo (BSON stores datetime natively). This broke every
# saga scenario that calls SagaContext.request_timeout() under Redis persistence.
# ---------------------------------------------------------------------------


class TestRedisDatetimeRoundTrip:
    def test_datetime_field_round_trips(self, redis_client: Redis) -> None:  # type: ignore[type-arg]
        repo: RedisPersistableRepository[UUID, SagaTimeout] = RedisPersistableRepository(
            redis_client, SagaTimeout, "timeouts_dt_test"
        )
        uow: UnitOfWork[Pipeline] = UnitOfWork(RedisDBContext(redis_client))

        fire_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
        timeout = SagaTimeout(
            saga_id=uuid4(),
            saga_type_name="Example",
            state="unused",
            fire_at=fire_at,
        )
        with uow(repo):
            repo.save(timeout)
            uow.commit()
        with uow(repo):
            fetched = repo.get_one(timeout.get_id())
        assert fetched.fire_at == fire_at
