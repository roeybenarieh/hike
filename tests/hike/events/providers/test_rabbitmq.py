"""Integration tests for RabbitMQEventPublisher and RabbitMQEventSubscriber.

Requires Docker. Run with::

    uv run pytest tests/hike/events/providers/test_rabbitmq.py -v
"""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pika
import pytest
from pika.adapters.blocking_connection import BlockingChannel
from testcontainers.core.container import DockerContainer  # pyright: ignore[reportMissingTypeStubs]

from hike.domain_event import DomainEvent, register_event
from hike.events.interfaces import IBlockingEventSubscriber, IEventHandler, IEventPublisher
from hike.events.providers.rabbitmq import RabbitMQEventPublisher, RabbitMQEventSubscriber
from tests.hike.events.providers.parity_suite import EventProviderParitySuite


class _RabbitMqContainer(DockerContainer):  # pyright: ignore[reportMissingTypeStubs]
    """RabbitMQ container using connection-based readiness polling instead of the
    deprecated @wait_container_is_ready decorator."""

    _PORT = 5672

    def __init__(self, image: str = "rabbitmq:3.13-alpine") -> None:
        super().__init__(image=image)  # pyright: ignore[reportUnknownMemberType]
        self.with_exposed_ports(self._PORT)  # pyright: ignore[reportUnknownMemberType]

    def get_connection_params(self) -> pika.ConnectionParameters:
        return pika.ConnectionParameters(
            host=self.get_container_host_ip(),  # pyright: ignore[reportUnknownMemberType]
            port=int(self.get_exposed_port(self._PORT)),  # pyright: ignore[reportUnknownMemberType]
        )

    def start(self) -> "_RabbitMqContainer":
        super().start()  # pyright: ignore[reportUnknownMemberType]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                conn = pika.BlockingConnection(self.get_connection_params())
                if conn.is_open:
                    conn.close()
                    return self
            except Exception:
                time.sleep(0.5)
        raise RuntimeError("RabbitMQ did not become ready within 30 s")


@register_event
@dataclass(frozen=True)
class ParcelShipped(DomainEvent):
    tracking_id: str
    recipient: str


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def rabbitmq_params() -> Iterator[pika.ConnectionParameters]:
    with _RabbitMqContainer("rabbitmq:3.13-alpine") as rmq:
        yield rmq.get_connection_params()


@pytest.fixture
def exchange() -> str:
    return f"test.events.{uuid.uuid4().hex[:8]}"


@pytest.fixture
def queue() -> str:
    return f"test.queue.{uuid.uuid4().hex[:8]}"


@pytest.fixture
def pub_channel(rabbitmq_params: pika.ConnectionParameters) -> Iterator[BlockingChannel]:
    conn = pika.BlockingConnection(rabbitmq_params)
    ch = conn.channel()
    assert isinstance(ch, BlockingChannel)
    yield ch
    try:
        conn.close()
    except Exception:
        pass


@pytest.fixture
def sub_channel(rabbitmq_params: pika.ConnectionParameters) -> Iterator[BlockingChannel]:
    conn = pika.BlockingConnection(rabbitmq_params)
    ch = conn.channel()
    assert isinstance(ch, BlockingChannel)
    yield ch
    try:
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Publisher tests
# ---------------------------------------------------------------------------


class TestRabbitMQEventPublisher:
    def test_publish_delivers_to_bound_queue(
        self,
        pub_channel: BlockingChannel,
        sub_channel: BlockingChannel,
        exchange: str,
        queue: str,
    ) -> None:
        # Set up the queue first so published messages are routed correctly.
        sub_channel.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)
        sub_channel.queue_declare(queue=queue, durable=True)
        sub_channel.queue_bind(exchange=exchange, queue=queue, routing_key="ParcelShipped")

        publisher = RabbitMQEventPublisher(pub_channel, exchange=exchange)
        publisher.publish([ParcelShipped(tracking_id="T1", recipient="Alice")])

        # Poll until the message appears (broker may need a moment to route it).
        method = None
        body: bytes | None = None
        properties = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            method, properties, body = sub_channel.basic_get(queue=queue, auto_ack=True)
            if method is not None:
                break
            time.sleep(0.05)

        assert method is not None, "no message in queue after publish"
        assert body is not None
        assert b'"Alice"' in body
        assert properties is not None
        assert str((properties.headers or {}).get("event_type", "")) == "ParcelShipped"


# ---------------------------------------------------------------------------
# Subscriber tests
# ---------------------------------------------------------------------------


class TestRabbitMQEventSubscriber:
    def test_handler_exception_nacks_and_requeues_message(
        self,
        rabbitmq_params: pika.ConnectionParameters,
        pub_channel: BlockingChannel,
        exchange: str,
        queue: str,
    ) -> None:
        """A failing handler must nack the message so it stays in the broker.

        The test uses a flaky handler that raises on the first delivery and
        succeeds on the second, confirming the message was requeued and
        redelivered without restarting the consumer.
        """
        conn = pika.BlockingConnection(rabbitmq_params)
        try:
            sub_ch = conn.channel()
            assert isinstance(sub_ch, BlockingChannel)
            subscriber = RabbitMQEventSubscriber(sub_ch, exchange=exchange, queue=queue)
            received: list[ParcelShipped] = []
            attempts = 0

            class _FlakyHandler(IEventHandler[ParcelShipped]):
                def handle(self, event: ParcelShipped) -> None:
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        raise RuntimeError("first attempt fails — should be nacked and requeued")
                    received.append(event)
                    subscriber.close()

            subscriber.subscribe(_FlakyHandler())

            publisher = RabbitMQEventPublisher(pub_channel, exchange=exchange)
            publisher.publish([ParcelShipped(tracking_id="T1", recipient="Bob")])

            t = threading.Thread(target=subscriber.start, daemon=True)
            t.start()
            t.join(timeout=30)
        finally:
            try:
                conn.close()
            except Exception:
                pass

        assert not t.is_alive(), "subscriber did not stop within 30 s"
        assert attempts == 2, f"expected 2 delivery attempts, got {attempts}"
        assert len(received) == 1
        assert received[0].recipient == "Bob"

    def test_handler_exception_does_not_stop_consumer(
        self,
        rabbitmq_params: pika.ConnectionParameters,
        pub_channel: BlockingChannel,
        exchange: str,
        queue: str,
    ) -> None:
        """Consumer keeps running after a nack — subsequent messages are still processed."""
        conn = pika.BlockingConnection(rabbitmq_params)
        try:
            sub_ch = conn.channel()
            assert isinstance(sub_ch, BlockingChannel)
            subscriber = RabbitMQEventSubscriber(sub_ch, exchange=exchange, queue=queue)
            received: list[ParcelShipped] = []

            class _Handler(IEventHandler[ParcelShipped]):
                def __init__(self) -> None:
                    self._count = 0

                def handle(self, event: ParcelShipped) -> None:
                    self._count += 1
                    # Fail on first delivery of T1; succeed on T2 and on T1's redelivery.
                    if event.tracking_id == "T1" and self._count == 1:
                        raise RuntimeError("fail T1 first time")
                    received.append(event)
                    if len(received) == 2:
                        subscriber.close()

            subscriber.subscribe(_Handler())

            publisher = RabbitMQEventPublisher(pub_channel, exchange=exchange)
            # Publish two messages; T1 will be nacked and requeued, T2 will be acked.
            publisher.publish([
                ParcelShipped(tracking_id="T1", recipient="Carol"),
                ParcelShipped(tracking_id="T2", recipient="Dave"),
            ])

            t = threading.Thread(target=subscriber.start, daemon=True)
            t.start()
            t.join(timeout=30)
        finally:
            try:
                conn.close()
            except Exception:
                pass

        assert not t.is_alive(), "subscriber did not stop within 30 s"
        assert len(received) == 2
        tracking_ids = {e.tracking_id for e in received}
        assert tracking_ids == {"T1", "T2"}


# ---------------------------------------------------------------------------
# Parity tests (IEventPublisher + IBlockingEventSubscriber interface)
# ---------------------------------------------------------------------------


class TestRabbitMQEventProviderParity(EventProviderParitySuite):
    @pytest.fixture
    def publisher(
        self, pub_channel: BlockingChannel, exchange: str
    ) -> IEventPublisher[DomainEvent]:
        return RabbitMQEventPublisher(pub_channel, exchange=exchange)

    @pytest.fixture
    def subscriber(
        self, sub_channel: BlockingChannel, exchange: str, queue: str
    ) -> IBlockingEventSubscriber:
        return RabbitMQEventSubscriber(sub_channel, exchange=exchange, queue=queue)
