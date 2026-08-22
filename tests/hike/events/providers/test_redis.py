"""Integration tests for RedisEventPublisher and RedisEventSubscriber.

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
def channel_prefix() -> str:
    return f"test.{uuid.uuid4().hex[:8]}"


def _start_subscriber(subscriber: RedisEventSubscriber) -> threading.Thread:
    t = threading.Thread(target=subscriber.start, daemon=True)
    t.start()
    time.sleep(0.1)  # allow subscribe() to register with the broker before publishing
    return t


# ---------------------------------------------------------------------------
# Publisher tests
# ---------------------------------------------------------------------------


class TestRedisEventPublisher:
    def test_publish_delivers_to_channel(
        self, redis_client: Redis, channel_prefix: str  # type: ignore[type-arg]
    ) -> None:
        import json
        channel = f"{channel_prefix}.PackageArrived"
        pubsub: Any = redis_client.pubsub()  # pyright: ignore[reportUnknownMemberType]
        pubsub.subscribe(channel)
        time.sleep(0.05)  # let subscription register

        publisher = RedisEventPublisher(redis_client, channel_prefix=channel_prefix)
        publisher.publish([PackageArrived(package_id="P1", location="London")])

        raw_msg: Any = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            raw_msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
            if raw_msg is not None:
                break
        pubsub.close()

        assert raw_msg is not None, "no message received on Redis channel"
        payload = json.loads(raw_msg["data"])
        assert payload["event_type"] == "PackageArrived"
        assert '"London"' in payload["data"]


# ---------------------------------------------------------------------------
# Subscriber tests
# ---------------------------------------------------------------------------


class TestRedisEventSubscriber:
    def test_handler_exception_does_not_stop_subscriber(
        self, redis_client: Redis, channel_prefix: str  # type: ignore[type-arg]
    ) -> None:
        """A failing handler must not crash the subscriber; it keeps processing messages.

        Note: Redis Pub/Sub is fire-and-forget — the failed message is NOT
        redelivered; only the subscriber staying alive is verified here.
        """
        subscriber = RedisEventSubscriber(redis_client, channel_prefix=channel_prefix)
        received: list[PackageArrived] = []

        class _FlakyHandler(IEventHandler[PackageArrived]):
            def __init__(self) -> None:
                self._first = True

            def handle(self, event: PackageArrived) -> None:
                if self._first:
                    self._first = False
                    raise RuntimeError("first message fails")
                received.append(event)
                subscriber.close()

        subscriber.subscribe(_FlakyHandler())
        t = _start_subscriber(subscriber)

        publisher = RedisEventPublisher(redis_client, channel_prefix=channel_prefix)
        publisher.publish([PackageArrived(package_id="P1", location="Fail")])
        time.sleep(0.1)
        publisher.publish([PackageArrived(package_id="P2", location="Rome")])

        t.join(timeout=10)
        assert not t.is_alive(), "subscriber did not stop within 10 s"
        # Only the second message was processed successfully.
        assert len(received) == 1
        assert received[0].package_id == "P2"


# ---------------------------------------------------------------------------
# Parity tests (IEventPublisher + IBlockingEventSubscriber interface)
# ---------------------------------------------------------------------------


class TestRedisEventProviderParity(EventProviderParitySuite):
    @pytest.fixture
    def publisher(
        self, redis_client: Redis, channel_prefix: str  # type: ignore[type-arg]
    ) -> IEventPublisher[DomainEvent]:
        return RedisEventPublisher(redis_client, channel_prefix=channel_prefix)

    @pytest.fixture
    def subscriber(
        self, redis_client: Redis, channel_prefix: str  # type: ignore[type-arg]
    ) -> IBlockingEventSubscriber:
        return RedisEventSubscriber(redis_client, channel_prefix=channel_prefix)
