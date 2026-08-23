"""Integration tests for RedisEventPublisher and RedisEventSubscriber (Redis Streams).

Requires Docker. Run with::

    uv run pytest tests/hike/events/providers/test_redis.py -v
"""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from redis import Redis
from testcontainers.community.redis import RedisContainer  # pyright: ignore[reportMissingTypeStubs]

from hike.domain_event import DomainEvent, register_event
from hike.events.interfaces import IBlockingEventSubscriber, IEventHandler, IEventPublisher
from hike.events.providers.redis import RedisEventPublisher, RedisEventSubscriber
from tests.hike.events.providers.parity_suite import EventProviderParitySuite

@register_event
@dataclass(frozen=True)
class PackageArrived(DomainEvent):
    package_id: str
    location: str


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def redis_client() -> Iterator[Redis]:  # type: ignore[type-arg]
    with RedisContainer("redis:7") as container:  # pyright: ignore[reportUnknownMemberType]
        client: Redis = container.get_client()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        yield client


@pytest.fixture
def stream_prefix() -> str:
    return f"test.{uuid.uuid4().hex[:8]}"


@pytest.fixture
def group() -> str:
    return f"test.group.{uuid.uuid4().hex[:8]}"


def _start_subscriber(subscriber: RedisEventSubscriber) -> threading.Thread:
    t = threading.Thread(target=subscriber.start, daemon=True)
    t.start()
    time.sleep(0.15)  # allow consumer group creation before publishing
    return t


# ---------------------------------------------------------------------------
# Publisher tests
# ---------------------------------------------------------------------------


class TestRedisEventPublisher:
    def test_publish_writes_to_stream(
        self, redis_client: Redis, stream_prefix: str  # type: ignore[type-arg]
    ) -> None:
        publisher = RedisEventPublisher(redis_client, stream_prefix=stream_prefix)
        publisher.publish([PackageArrived(package_id="P1", location="London")])

        stream_key = f"{stream_prefix}.PackageArrived"
        entries: Any = redis_client.xread(  # pyright: ignore[reportUnknownMemberType]
            {stream_key: 0}, count=1
        )
        assert entries, "no entry in stream after publish"
        _, messages = entries[0]
        _, fields = messages[0]

        def _f(key: str) -> str:
            v: Any = fields.get(key.encode()) or fields.get(key)
            return v.decode() if isinstance(v, bytes) else str(v or "")

        assert _f("event_type") == "PackageArrived"
        assert '"London"' in _f("data")


# ---------------------------------------------------------------------------
# Subscriber tests
# ---------------------------------------------------------------------------


class TestRedisEventSubscriber:
    def test_handler_exception_nacks_and_redelivers_message(
        self,
        redis_client: Redis,  # type: ignore[type-arg]
        stream_prefix: str,
        group: str,
    ) -> None:
        """A failing handler must not ack the message so it is redelivered from the PEL.

        Consumer group semantics: the first delivery raises, leaving the
        message unacknowledged in the Pending Entry List.  The subscriber
        re-reads it on the next iteration and succeeds.
        """
        subscriber = RedisEventSubscriber(
            redis_client, stream_prefix=stream_prefix, group=group
        )
        received: list[PackageArrived] = []
        attempts = 0

        class _FlakyHandler(IEventHandler[PackageArrived]):
            def handle(self, event: PackageArrived) -> None:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RuntimeError("first attempt fails — message must be redelivered")
                received.append(event)
                subscriber.close()

        subscriber.subscribe(_FlakyHandler())
        t = _start_subscriber(subscriber)

        publisher = RedisEventPublisher(redis_client, stream_prefix=stream_prefix)
        publisher.publish([PackageArrived(package_id="P1", location="London")])

        t.join(timeout=30)

        assert not t.is_alive(), "subscriber did not stop within 30 s"
        assert attempts == 2, f"expected 2 delivery attempts, got {attempts}"
        assert len(received) == 1
        assert received[0].location == "London"

    def test_handler_exception_does_not_stop_subscriber(
        self,
        redis_client: Redis,  # type: ignore[type-arg]
        stream_prefix: str,
        group: str,
    ) -> None:
        """Consumer keeps running after a handler failure.

        P1 fails on first delivery (nacked, requeued to PEL); P2 succeeds.
        P1 is redelivered from the PEL and also succeeds.  Both messages end
        up in *received*.
        """
        subscriber = RedisEventSubscriber(
            redis_client, stream_prefix=stream_prefix, group=group
        )
        received: list[PackageArrived] = []

        class _Handler(IEventHandler[PackageArrived]):
            def __init__(self) -> None:
                self._count = 0

            def handle(self, event: PackageArrived) -> None:
                self._count += 1
                if event.package_id == "P1" and self._count == 1:
                    raise RuntimeError("fail P1 first time")
                received.append(event)
                if len(received) == 2:
                    subscriber.close()

        subscriber.subscribe(_Handler())
        t = _start_subscriber(subscriber)

        publisher = RedisEventPublisher(redis_client, stream_prefix=stream_prefix)
        publisher.publish([
            PackageArrived(package_id="P1", location="Fail"),
            PackageArrived(package_id="P2", location="Rome"),
        ])

        t.join(timeout=30)

        assert not t.is_alive(), "subscriber did not stop within 30 s"
        assert len(received) == 2
        assert {e.package_id for e in received} == {"P1", "P2"}


# ---------------------------------------------------------------------------
# Parity tests (IEventPublisher + IBlockingEventSubscriber interface)
# ---------------------------------------------------------------------------


class TestRedisEventProviderParity(EventProviderParitySuite):
    @pytest.fixture
    def publisher(
        self, redis_client: Redis, stream_prefix: str  # type: ignore[type-arg]
    ) -> IEventPublisher[DomainEvent]:
        return RedisEventPublisher(redis_client, stream_prefix=stream_prefix)

    @pytest.fixture
    def subscriber(
        self, redis_client: Redis, stream_prefix: str, group: str  # type: ignore[type-arg]
    ) -> IBlockingEventSubscriber:
        return RedisEventSubscriber(redis_client, stream_prefix=stream_prefix, group=group)
