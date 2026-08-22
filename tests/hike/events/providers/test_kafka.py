"""Integration tests for KafkaEventPublisher and KafkaEventSubscriber.

Requires Docker. Run with::

    uv run pytest tests/hike/events/providers/test_kafka.py -v
"""
from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from confluent_kafka import Consumer, Producer  # pyright: ignore[reportMissingModuleSource]
from testcontainers.community.kafka import KafkaContainer  # pyright: ignore[reportMissingTypeStubs]

from hike.domain_event import DomainEvent, register_event
from hike.events.interfaces import IBlockingEventSubscriber, IEventHandler, IEventPublisher
from hike.events.providers.kafka import KafkaEventPublisher, KafkaEventSubscriber
from tests.hike.events.providers.parity_suite import EventProviderParitySuite

@register_event
@dataclass(frozen=True)
class VesselSailed(DomainEvent):
    vessel_id: str
    destination: str


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def kafka_bootstrap() -> Iterator[str]:
    with KafkaContainer().with_kraft() as kafka:  # pyright: ignore[reportUnknownMemberType]
        yield str(kafka.get_bootstrap_server())  # pyright: ignore[reportUnknownMemberType]


@pytest.fixture
def topic_prefix() -> str:
    return f"test.{uuid.uuid4().hex[:8]}"


@pytest.fixture
def producer(kafka_bootstrap: str) -> Producer:
    return Producer({"bootstrap.servers": kafka_bootstrap})


def _make_consumer(bootstrap: str, group_id: str | None = None) -> Consumer:
    return Consumer({
        "bootstrap.servers": bootstrap,
        "group.id": group_id or uuid.uuid4().hex,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": "false",
    })


# ---------------------------------------------------------------------------
# Publisher tests
# ---------------------------------------------------------------------------


class TestKafkaEventPublisher:
    def test_publish_delivers_to_topic(
        self, producer: Producer, kafka_bootstrap: str, topic_prefix: str
    ) -> None:
        publisher = KafkaEventPublisher(producer, topic_prefix=topic_prefix)
        publisher.publish([VesselSailed(vessel_id="v1", destination="Lisbon")])

        consumer = _make_consumer(kafka_bootstrap)
        topic = f"{topic_prefix}.VesselSailed"
        consumer.subscribe([topic])
        msg = None
        try:
            for _ in range(30):
                msg = consumer.poll(1.0)
                if msg is not None:
                    break
        finally:
            consumer.close()

        assert msg is not None, "no message received from Kafka within 30 s"
        assert msg.error() is None
        raw_headers = msg.headers() or []
        header_map = {k: (v.decode() if isinstance(v, bytes) else v or "") for k, v in raw_headers}
        assert header_map.get("event_type") == "VesselSailed"
        assert b'"Lisbon"' in (msg.value() or b"")

    def test_publish_multiple_events(
        self, producer: Producer, kafka_bootstrap: str, topic_prefix: str
    ) -> None:
        publisher = KafkaEventPublisher(producer, topic_prefix=topic_prefix)
        publisher.publish([
            VesselSailed(vessel_id="v1", destination="Porto"),
            VesselSailed(vessel_id="v2", destination="Faro"),
        ])

        consumer = _make_consumer(kafka_bootstrap)
        topic = f"{topic_prefix}.VesselSailed"
        consumer.subscribe([topic])
        received: list[bytes] = []
        try:
            for _ in range(30):
                msg = consumer.poll(1.0)
                if msg is not None and msg.error() is None and msg.value():
                    received.append(msg.value())  # type: ignore[arg-type]
                if len(received) == 2:
                    break
        finally:
            consumer.close()

        assert len(received) == 2


# ---------------------------------------------------------------------------
# Subscriber tests
# ---------------------------------------------------------------------------


class TestKafkaEventSubscriber:
    def test_handler_exception_leaves_offset_uncommitted_for_redelivery(
        self, producer: Producer, kafka_bootstrap: str, topic_prefix: str
    ) -> None:
        """A failing handler must not commit the offset so the message is redelivered.

        Consumer 1 (same group) fails to process the message — offset is not
        committed.  Consumer 2 (same group, auto.offset.reset=earliest) starts
        from the same uncommitted position and receives the message again.
        """
        group_id = uuid.uuid4().hex
        publisher = KafkaEventPublisher(producer, topic_prefix=topic_prefix)
        publisher.publish([VesselSailed(vessel_id="v1", destination="Oslo")])

        # Consumer 1: handler raises → no commit.
        consumer1 = _make_consumer(kafka_bootstrap, group_id=group_id)
        subscriber1 = KafkaEventSubscriber(consumer1, topic_prefix=topic_prefix)

        class _FailingHandler(IEventHandler[VesselSailed]):
            def handle(self, event: VesselSailed) -> None:
                subscriber1.close()
                raise RuntimeError("processing failed — must not commit")

        subscriber1.subscribe(_FailingHandler())
        t1 = threading.Thread(target=subscriber1.start, daemon=True)
        t1.start()
        t1.join(timeout=30)
        assert not t1.is_alive()

        # Consumer 2 (same group): should receive the same message again.
        consumer2 = _make_consumer(kafka_bootstrap, group_id=group_id)
        subscriber2 = KafkaEventSubscriber(consumer2, topic_prefix=topic_prefix)
        received: list[VesselSailed] = []

        class _SuccessHandler(IEventHandler[VesselSailed]):
            def handle(self, event: VesselSailed) -> None:
                received.append(event)
                subscriber2.close()

        subscriber2.subscribe(_SuccessHandler())
        t2 = threading.Thread(target=subscriber2.start, daemon=True)
        t2.start()
        t2.join(timeout=30)

        assert not t2.is_alive(), "second consumer did not receive redelivered message within 30 s"
        assert len(received) == 1
        assert received[0].vessel_id == "v1"

    def test_handler_exception_does_not_stop_consumer(
        self, producer: Producer, kafka_bootstrap: str, topic_prefix: str
    ) -> None:
        """Consumer keeps running after a handler failure — subsequent messages are still processed."""
        publisher = KafkaEventPublisher(producer, topic_prefix=topic_prefix)
        publisher.publish([
            VesselSailed(vessel_id="v1", destination="Fail"),
            VesselSailed(vessel_id="v2", destination="Oslo"),
        ])

        consumer = _make_consumer(kafka_bootstrap)
        subscriber = KafkaEventSubscriber(consumer, topic_prefix=topic_prefix)
        received: list[VesselSailed] = []

        class _Handler(IEventHandler[VesselSailed]):
            def handle(self, event: VesselSailed) -> None:
                if event.destination == "Fail":
                    raise RuntimeError("intentional failure")
                received.append(event)
                subscriber.close()

        subscriber.subscribe(_Handler())
        t = threading.Thread(target=subscriber.start, daemon=True)
        t.start()
        t.join(timeout=30)

        assert not t.is_alive(), "subscriber did not stop within 30 s"
        assert len(received) == 1
        assert received[0].vessel_id == "v2"


# ---------------------------------------------------------------------------
# Parity tests (IEventPublisher + IBlockingEventSubscriber interface)
# ---------------------------------------------------------------------------


class TestKafkaEventProviderParity(EventProviderParitySuite):
    @pytest.fixture
    def publisher(self, producer: Producer, topic_prefix: str) -> IEventPublisher[DomainEvent]:
        return KafkaEventPublisher(producer, topic_prefix=topic_prefix)

    @pytest.fixture
    def subscriber(self, kafka_bootstrap: str, topic_prefix: str) -> IBlockingEventSubscriber:
        consumer = _make_consumer(kafka_bootstrap)
        return KafkaEventSubscriber(consumer, topic_prefix=topic_prefix)
