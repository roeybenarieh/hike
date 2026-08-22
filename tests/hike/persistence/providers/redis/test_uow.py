"""Integration tests for UnitOfWork + RedisRepository against a real Redis
instance managed by testcontainers.

Run with::

    uv run pytest tests/hike/persistence/providers/redis/test_uow.py -v
"""
from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from redis import Redis
from redis.client import Pipeline
from testcontainers.community.redis import RedisContainer  # pyright: ignore[reportMissingTypeStubs]

from hike.persistence.providers.redis import RedisDBContext, RedisRepository
from hike.persistence.uow import UnitOfWork

from tests.hike.conftest import Boat, Journey
from tests.hike.persistence.providers.parity_suite import RepositoryParitySuite

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


class TestRedisRepositoryParity(RepositoryParitySuite):
    @pytest.fixture
    def uow(self, redis_client: Redis) -> UnitOfWork[Pipeline]:
        return UnitOfWork(RedisDBContext(redis_client))

    @pytest.fixture
    def repo(self, redis_client: Redis) -> RedisRepository[UUID, Boat]:
        return RedisRepository(redis_client, Boat, KEY_PREFIX)

    @pytest.fixture
    def journey_repo(self, redis_client: Redis) -> RedisRepository[UUID, Journey]:
        return RedisRepository(redis_client, Journey, JOURNEY_KEY_PREFIX)
