"""Integration tests for RedisEventPublisher and RedisEventSubscriber (Redis Streams).

Requires Docker. Run with::

    uv run pytest tests/hike/events/providers/test_redis.py -v
"""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, ClassVar

import pytest
from redis import Redis
from testcontainers.community.redis import RedisContainer  # pyright: ignore[reportMissingTypeStubs]

from hike.events.integration_event import IntegrationEvent
from hike.events.interfaces import IExternalEventSubscriber, IEventHandler, IEventPublisher
from hike.events.providers.redis import RedisEventBus, RedisEventPublisher, RedisEventSubscriber
from tests.hike.events.providers.parity_suite import EventBusParitySuite, EventProviderParitySuite


@dataclass(frozen=True, kw_only=True, eq=False)
class PackageArrived(IntegrationEvent):
    package_id: str
    location: str
    source: ClassVar[str] = "//test-service"
    version: int = 1


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
    time.sleep(0.15)
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
        """Contract 1 (ack/nack): failing handler leaves message in PEL → redelivered."""
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
                    raise RuntimeError("first attempt fails — message must stay in PEL")
                received.append(event)
                subscriber.cleanup()

        subscriber.subscribe(_FlakyHandler())  # type: ignore[arg-type]
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
        """Consumer keeps running after a handler failure."""
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
                    subscriber.cleanup()

        subscriber.subscribe(_Handler())  # type: ignore[arg-type]
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

    def test_failover_via_xautoclaim(
        self,
        redis_client: Redis,  # type: ignore[type-arg]
        stream_prefix: str,
        group: str,
    ) -> None:
        """Contract 2 (failover): crashed consumer's in-flight message is claimed by a peer.

        Sub1's handler blocks indefinitely — the message stays unacked in sub1's
        Pending Entry List.  Sub2 (same group, short claim_idle_ms) calls
        XAUTOCLAIM and steals the idle message, then processes it successfully.
        """
        claim_idle_ms = 200

        sub1 = RedisEventSubscriber(
            redis_client,
            stream_prefix=stream_prefix,
            group=group,
            consumer="consumer-1",
            claim_idle_ms=claim_idle_ms,
        )
        sub2 = RedisEventSubscriber(
            redis_client,
            stream_prefix=stream_prefix,
            group=group,
            consumer="consumer-2",
            claim_idle_ms=claim_idle_ms,
        )

        sub1_received = threading.Event()
        sub2_received = threading.Event()

        class _BlockingHandler(IEventHandler[PackageArrived]):
            def handle(self, event: PackageArrived) -> None:
                sub1_received.set()
                # Block until the test is over — message stays unacked in PEL.
                time.sleep(30)

        class _RecoveryHandler(IEventHandler[PackageArrived]):
            def handle(self, event: PackageArrived) -> None:
                sub2_received.set()
                sub2.cleanup()

        sub1.subscribe(_BlockingHandler())  # type: ignore[arg-type]
        sub2.subscribe(_RecoveryHandler())  # type: ignore[arg-type]

        # Start sub1 first so it creates the consumer group.
        t1 = _start_subscriber(sub1)

        publisher = RedisEventPublisher(redis_client, stream_prefix=stream_prefix)
        publisher.publish([PackageArrived(package_id="P1", location="Paris")])

        # Wait for sub1 to receive the message (its handler will then block).
        assert sub1_received.wait(timeout=10), "sub1 did not receive the message"

        # Wait longer than claim_idle_ms so the message is eligible for claiming.
        time.sleep(0.4)

        # Start sub2 — it should XAUTOCLAIM the idle message from sub1's PEL.
        t2 = _start_subscriber(sub2)
        assert sub2_received.wait(timeout=10), (
            "sub2 did not recover the message via XAUTOCLAIM within 10 s"
        )

        sub1.cleanup()
        t1.join(timeout=5)
        t2.join(timeout=5)


# ---------------------------------------------------------------------------
# Parity tests (three contracts + smoke tests)
# ---------------------------------------------------------------------------


class TestRedisEventProviderParity(EventProviderParitySuite):
    @pytest.fixture
    def publisher(
        self, redis_client: Redis, stream_prefix: str  # type: ignore[type-arg]
    ) -> IEventPublisher[IntegrationEvent]:
        return RedisEventPublisher(redis_client, stream_prefix=stream_prefix)

    @pytest.fixture
    def make_subscriber(
        self,
        redis_client: Redis,  # type: ignore[type-arg]
        stream_prefix: str,
        group: str,
    ) -> Callable[[], IExternalEventSubscriber[IntegrationEvent]]:
        """Factory that creates subscribers all sharing the same consumer group.

        claim_idle_ms=200 ensures the ack/nack contract test (which sleeps 0.5 s
        between sub1 and sub2) can rely on XAUTOCLAIM to reclaim sub1's idle
        message.
        """
        def factory() -> IExternalEventSubscriber[IntegrationEvent]:
            return RedisEventSubscriber(
                redis_client,
                stream_prefix=stream_prefix,
                group=group,
                claim_idle_ms=200,
            )

        return factory


class TestRedisEventBusParity(EventBusParitySuite):
    @pytest.fixture
    def make_event_bus(
        self,
        redis_client: Redis,  # type: ignore[type-arg]
        stream_prefix: str,
        group: str,
    ) -> Callable[[], RedisEventBus]:
        def factory() -> RedisEventBus:
            return RedisEventBus(
                redis_client,
                stream_prefix=stream_prefix,
                group=group,
                claim_idle_ms=200,
            )

        return factory
