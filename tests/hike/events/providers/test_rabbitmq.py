"""Integration tests for RabbitMQEventPublisher and RabbitMQEventSubscriber.

Requires Docker. Run with::

    uv run pytest tests/hike/events/providers/test_rabbitmq.py -v
"""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pika
import pytest
from pika.adapters.blocking_connection import BlockingChannel
from testcontainers.core.container import DockerContainer  # pyright: ignore[reportMissingTypeStubs]

from hike.domain_event import DomainEvent
from hike.events.interfaces import IExternalEventSubscriber, IEventHandler, IEventPublisher
from hike.events.providers.rabbitmq import RabbitMQEventPublisher, RabbitMQEventSubscriber
from tests.hike.events.providers.parity_suite import EventProviderParitySuite


class _RabbitMqContainer(DockerContainer):  # pyright: ignore[reportMissingTypeStubs]
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
        sub_channel.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)
        sub_channel.queue_declare(queue=queue, durable=True)
        sub_channel.queue_bind(exchange=exchange, queue=queue, routing_key="ParcelShipped")

        publisher = RabbitMQEventPublisher(pub_channel, exchange=exchange)
        publisher.publish([ParcelShipped(tracking_id="T1", recipient="Alice")])

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
        """Contract 1 (ack/nack): failing handler nacks → broker requeues → redelivered."""
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
                    subscriber.cleanup()

            subscriber.subscribe(_FlakyHandler())  # type: ignore[arg-type]

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

    def test_failover_on_crash(
        self,
        rabbitmq_params: pika.ConnectionParameters,
        pub_channel: BlockingChannel,
        exchange: str,
        queue: str,
    ) -> None:
        """Contract 2 (failover): message in-flight to a crashed connection is requeued and
        delivered to another subscriber on the same queue.

        Simulates a crash by doing a manual basic_get (no auto-ack) on a
        separate connection and then closing that connection without acking.
        RabbitMQ requeues the unacked delivery.  The actual
        RabbitMQEventSubscriber on conn2 should then pick it up.
        """
        # Publish first so there is a message in the queue.
        # Declare exchange/queue manually so we can basic_get below.
        setup_conn = pika.BlockingConnection(rabbitmq_params)
        setup_ch = setup_conn.channel()
        setup_ch.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)
        setup_ch.queue_declare(queue=queue, durable=True)
        setup_ch.queue_bind(exchange=exchange, queue=queue, routing_key="ParcelShipped")
        setup_conn.close()

        publisher = RabbitMQEventPublisher(pub_channel, exchange=exchange)
        publisher.publish([ParcelShipped(tracking_id="T1", recipient="Carol")])

        # "Crash": get the message without acking, then close the connection.
        crash_conn = pika.BlockingConnection(rabbitmq_params)
        crash_ch = crash_conn.channel()
        method = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            method, _, _ = crash_ch.basic_get(queue=queue, auto_ack=False)
            if method is not None:
                break
            time.sleep(0.1)
        assert method is not None, "message was not delivered to crash_conn within 10 s"
        # Close the connection without acking → RabbitMQ requeues the message.
        crash_conn.close()

        # The real subscriber should now receive the requeued message.
        recovery_conn = pika.BlockingConnection(rabbitmq_params)
        try:
            recovery_ch = recovery_conn.channel()
            assert isinstance(recovery_ch, BlockingChannel)
            sub = RabbitMQEventSubscriber(recovery_ch, exchange=exchange, queue=queue)
            received: list[ParcelShipped] = []

            class _RecoveryHandler(IEventHandler[ParcelShipped]):
                def handle(self, event: ParcelShipped) -> None:
                    received.append(event)
                    sub.cleanup()

            sub.subscribe(_RecoveryHandler())  # type: ignore[arg-type]
            t = threading.Thread(target=sub.start, daemon=True)
            t.start()
            t.join(timeout=15)
        finally:
            try:
                recovery_conn.close()
            except Exception:
                pass

        assert not t.is_alive(), "subscriber did not process the requeued message within 15 s"
        assert len(received) == 1
        assert received[0].tracking_id == "T1"

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
                    if event.tracking_id == "T1" and self._count == 1:
                        raise RuntimeError("fail T1 first time")
                    received.append(event)
                    if len(received) == 2:
                        subscriber.cleanup()

            subscriber.subscribe(_Handler())  # type: ignore[arg-type]

            publisher = RabbitMQEventPublisher(pub_channel, exchange=exchange)
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
        assert {e.tracking_id for e in received} == {"T1", "T2"}


# ---------------------------------------------------------------------------
# Parity tests (three contracts + smoke tests)
# ---------------------------------------------------------------------------


class TestRabbitMQEventProviderParity(EventProviderParitySuite):
    @pytest.fixture
    def publisher(
        self, pub_channel: BlockingChannel, exchange: str
    ) -> IEventPublisher[DomainEvent]:
        return RabbitMQEventPublisher(pub_channel, exchange=exchange)

    @pytest.fixture
    def make_subscriber(
        self,
        rabbitmq_params: pika.ConnectionParameters,
        exchange: str,
        queue: str,
    ) -> Iterator[Callable[[], IExternalEventSubscriber]]:
        """Factory that creates fresh subscribers all sharing the same queue."""
        connections: list[pika.BlockingConnection] = []

        def factory() -> IExternalEventSubscriber:
            conn = pika.BlockingConnection(rabbitmq_params)
            connections.append(conn)
            ch = conn.channel()
            assert isinstance(ch, BlockingChannel)
            return RabbitMQEventSubscriber(ch, exchange=exchange, queue=queue)

        yield factory

        for conn in connections:
            try:
                conn.close()
            except Exception:
                pass
