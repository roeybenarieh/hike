"""Integration tests for FlightBookingSaga across all persistence × event provider combinations.

Runs all 4 saga scenarios (happy path, failure, timeout, not-found) against every
combination of real infrastructure providers:

  Persistence: PyMongo, Redis
  Events:      RabbitMQ, Kafka, Redis Streams

6 combinations in total (2 × 3).  SQLAlchemy is omitted because
``ISQLAlchemyMapper`` currently targets ``Entity`` subclasses only, and
``SagaData`` / ``SagaTimeout`` are plain ``DataclassPersistable`` types.

Requires Docker.  Run with::

    uv run pytest tests/hike/events/saga/test_saga_integration.py -v
"""
from __future__ import annotations

import multiprocessing
import sys
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pika
import pytest
from confluent_kafka import Consumer, Producer  # pyright: ignore[reportMissingModuleSource]
from pika.adapters.blocking_connection import BlockingChannel
from pymongo import MongoClient
from redis import Redis

from testcontainers.core.container import DockerContainer  # pyright: ignore[reportMissingTypeStubs]
from testcontainers.core.wait_strategies import LogMessageWaitStrategy  # pyright: ignore[reportMissingTypeStubs]
from testcontainers.community.kafka import KafkaContainer  # pyright: ignore[reportMissingTypeStubs]
from testcontainers.community.redis import RedisContainer  # pyright: ignore[reportMissingTypeStubs]

from hike import UnitOfWork
from hike.events.interfaces import IExternalEventSubscriber
from hike.events.interfaces.publisher import IEventPublisher
from hike.events.providers.kafka import KafkaEventPublisher, KafkaEventSubscriber
from hike.events.providers.rabbitmq import RabbitMQEventPublisher, RabbitMQEventSubscriber
from hike.events.providers.redis import RedisEventPublisher, RedisEventSubscriber
from hike.events.saga import SagaManager, SagaTimeout, TimeoutManager
from hike.persistence.providers.pymongo import PyMongoDBContext, PyMongoPersistableRepository, PyMongoRepository
from hike.persistence.providers.redis import RedisDBContext, RedisPersistableRepository, RedisRepository
from hike.events.interfaces.background_task import IBackgroundTasks, Task
from hike.runner import ThreadedBackgroundTaskRunner, ProcessBackgroundTaskRunner

# ---------------------------------------------------------------------------
# Pull example types into test scope
# ---------------------------------------------------------------------------

_EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "../../../../examples/saga")
if _EXAMPLES_DIR not in sys.path:
    sys.path.insert(0, _EXAMPLES_DIR)

from booking_service import FlightBookingSaga  # type: ignore[import-not-found]
from payment_service import PaymentService  # type: ignore[import-not-found]
from shared import (  # type: ignore[import-not-found]
    FlightBookingData,
    FlightSeat,
    PaymentReceived,
)

# ---------------------------------------------------------------------------
# Test context
# ---------------------------------------------------------------------------


@dataclass
class SagaTestContext:
    """Everything needed to run a single saga scenario end-to-end."""

    seat_repo: Any
    seat_uow: UnitOfWork[Any]
    publisher: IEventPublisher[Any]
    saga_manager: SagaManager[FlightBookingData]
    booking_sub: IExternalEventSubscriber[Any]
    payment_sub: IExternalEventSubscriber[Any]
    timeout_manager: TimeoutManager
    runner: ThreadedBackgroundTaskRunner
    completed: threading.Event  # threading.Event; signalled by SagaManager.on_complete

    def start(self, *, include_payment: bool = True) -> None:
        """Start background tasks and wait for subscriptions to register."""
        providers = [self.booking_sub, self.timeout_manager]
        if include_payment:
            providers.append(self.payment_sub)
        self.runner.start(*providers)
        time.sleep(0.3)

    def stop(self) -> None:
        self.runner.stop()

    def add_seat(self) -> UUID:
        seat = FlightSeat()
        with self.seat_uow(self.seat_repo, auto_commit=True):
            self.seat_repo.save(seat)
        return seat.id.value

    def reserve(
        self,
        seat_id: UUID,
        *,
        flight_id: str,
        passenger: str,
        card_token: str,
        amount: float,
    ) -> None:
        with self.seat_uow(self.seat_repo, auto_commit=True):
            seat = self.seat_repo.get_one(seat_id)
            seat.reserve(
                flight_id=flight_id,
                passenger=passenger,
                card_token=card_token,
                amount=amount,
            )
            self.seat_repo.update(seat)

    def wait(self, timeout: float = 15.0) -> bool:
        return self.completed.wait(timeout=timeout)


# ---------------------------------------------------------------------------
# Build a wired SagaTestContext from already-created repos/publishers
# ---------------------------------------------------------------------------


def _build_context(
    *,
    seat_repo: Any,
    seat_uow: UnitOfWork[Any],
    saga_repo: Any,
    saga_uow: UnitOfWork[Any],
    timeout_repo: Any,
    timeout_uow: UnitOfWork[Any],
    tm_timeout_repo: Any | None = None,
    tm_timeout_uow: UnitOfWork[Any] | None = None,
    publisher: IEventPublisher[Any],
    booking_sub: IExternalEventSubscriber[Any],
    payment_sub: IExternalEventSubscriber[Any],
) -> SagaTestContext:
    FlightBookingSaga.seat_repo = seat_repo  # type: ignore[assignment]

    completed: threading.Event = threading.Event()

    saga_manager: SagaManager[FlightBookingData] = SagaManager(
        FlightBookingSaga,
        saga_repo,
        publisher=publisher,
        timeout_repo=timeout_repo,
        uow=saga_uow,
        timeout_uow=timeout_uow,
        on_complete=lambda _: completed.set(),
    )

    payment_service = PaymentService(publisher)

    # TimeoutManager gets its own repo AND UoW so it never shares mutable
    # state (_session field) with SagaManager.  Both run on different threads;
    # concurrent repo.session = ctx.session assignments corrupt each other's
    # transaction context (MongoDB: "Transaction committed" / Redis: wrong pipeline).
    tm_repo = tm_timeout_repo if tm_timeout_repo is not None else timeout_repo
    tm_uow = tm_timeout_uow if tm_timeout_uow is not None else timeout_uow
    timeout_manager = TimeoutManager(tm_repo, poll_interval=0.05, uow=tm_uow)
    timeout_manager.register(saga_manager)

    saga_manager.subscribe_to(booking_sub)
    payment_sub.subscribe(payment_service)

    return SagaTestContext(
        seat_repo=seat_repo,
        seat_uow=seat_uow,
        publisher=publisher,
        saga_manager=saga_manager,
        booking_sub=booking_sub,
        payment_sub=payment_sub,
        timeout_manager=timeout_manager,
        runner=ThreadedBackgroundTaskRunner(),
        completed=completed,
    )


# ===========================================================================
# Container fixtures (session-scoped — one container per pytest session)
# ===========================================================================


# ---------------------------------------------------------------------------
# MongoDB replica set
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def mongo_client() -> Iterator[MongoClient[Any]]:
    with (
        DockerContainer("mongo:7")
        .with_command("--replSet rs0 --bind_ip_all")
        .with_exposed_ports(27017)
        .waiting_for(LogMessageWaitStrategy("Waiting for connections")) as container  # pyright: ignore[reportUnknownMemberType]
    ):
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(27017))

        init_client: MongoClient[Any] = MongoClient(host=host, port=port, directConnection=True)
        init_client.admin.command(  # pyright: ignore[reportUnknownMemberType]
            "replSetInitiate",
            {"_id": "rs0", "members": [{"_id": 0, "host": "127.0.0.1:27017"}]},
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                hello = init_client.admin.command("hello")  # pyright: ignore[reportUnknownMemberType]
                if hello.get("isWritablePrimary"):
                    break
            except Exception:
                pass
            time.sleep(0.5)
        init_client.close()

        client: MongoClient[Any] = MongoClient(
            host=host, port=port, directConnection=True, uuidRepresentation="standard"
        )
        try:
            yield client
        finally:
            client.close()


# ---------------------------------------------------------------------------
# RabbitMQ
# ---------------------------------------------------------------------------


class _RabbitMQContainer(DockerContainer):  # pyright: ignore[reportMissingTypeStubs]
    _PORT = 5672

    def __init__(self) -> None:
        super().__init__(image="rabbitmq:3.13-alpine")  # pyright: ignore[reportUnknownMemberType]
        self.with_exposed_ports(self._PORT)  # pyright: ignore[reportUnknownMemberType]

    def connection_params(self) -> pika.ConnectionParameters:
        return pika.ConnectionParameters(
            host=self.get_container_host_ip(),  # pyright: ignore[reportUnknownMemberType]
            port=int(self.get_exposed_port(self._PORT)),  # pyright: ignore[reportUnknownMemberType]
        )

    def start(self) -> "_RabbitMQContainer":
        super().start()  # pyright: ignore[reportUnknownMemberType]
        # Wait until RabbitMQ can accept 3 concurrent connections — the number
        # _rabbitmq_event_stack needs.  A single-connection probe passes too
        # early; the broker still rejects additional connections for ~1 s after.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            conns: list[pika.BlockingConnection] = []
            try:
                for _ in range(3):
                    conns.append(pika.BlockingConnection(self.connection_params()))
                if all(c.is_open for c in conns):
                    return self
            except Exception:
                pass
            finally:
                for c in conns:
                    try:
                        c.close()
                    except Exception:
                        pass
            time.sleep(0.5)
        raise RuntimeError("RabbitMQ did not become ready within 60 s")


@pytest.fixture(scope="session")
def rabbitmq_params() -> Iterator[pika.ConnectionParameters]:
    try:
        with _RabbitMQContainer() as rmq:
            yield rmq.connection_params()
    except RuntimeError as exc:
        pytest.skip(str(exc))


# ---------------------------------------------------------------------------
# Kafka
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def kafka_bootstrap() -> Iterator[str]:
    kafka: KafkaContainer = KafkaContainer().with_kraft()  # pyright: ignore[reportUnknownMemberType]
    try:
        kafka.start(timeout=60)  # pyright: ignore[reportUnknownMemberType]
    except Exception as exc:
        pytest.skip(f"Kafka container did not become ready: {exc}")
    try:
        yield str(kafka.get_bootstrap_server())  # pyright: ignore[reportUnknownMemberType]
    finally:
        kafka.stop()  # pyright: ignore[reportUnknownMemberType]


# ---------------------------------------------------------------------------
# Redis (two containers: one for persistence, one for events — kept separate
# so namespace never overlaps when a test uses Redis for both)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def redis_persist_client() -> Iterator[Redis]:  # type: ignore[type-arg]
    with RedisContainer("redis:7") as container:  # pyright: ignore[reportUnknownMemberType]
        client: Redis = container.get_client()  # type: ignore[type-arg] # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        yield client  # type: ignore[misc]


@pytest.fixture(scope="session")
def redis_events_client() -> Iterator[Redis]:  # type: ignore[type-arg]
    with RedisContainer("redis:7") as container:  # pyright: ignore[reportUnknownMemberType]
        client: Redis = container.get_client()  # type: ignore[type-arg] # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        yield client  # type: ignore[misc]


# ===========================================================================
# Per-test context factories
# ===========================================================================

# --- MongoDB persistence helpers ------------------------------------------


def _mongo_context(client: MongoClient[Any], prefix: str, publisher: IEventPublisher[Any]) -> dict[str, Any]:
    """Return repo/UoW stacks for one test using MongoDB."""
    db = client[f"saga_test_{prefix}"]

    seat_col = db["seats"]
    seat_col.create_index("id", unique=True)
    seat_repo: PyMongoRepository[UUID, FlightSeat] = PyMongoRepository(seat_col, FlightSeat)  # type: ignore[arg-type]
    seat_ctx = PyMongoDBContext(client)
    seat_uow: UnitOfWork[Any] = UnitOfWork(seat_ctx, event_publisher=publisher)

    saga_col = db["saga_data"]
    saga_col.create_index([("flight_id", 1)])
    saga_repo: PyMongoPersistableRepository[UUID, FlightBookingData] = PyMongoPersistableRepository(saga_col, FlightBookingData)  # type: ignore[arg-type]
    saga_ctx = PyMongoDBContext(client)
    saga_uow: UnitOfWork[Any] = UnitOfWork(saga_ctx)

    timeout_col = db["saga_timeouts"]
    timeout_repo: PyMongoPersistableRepository[UUID, SagaTimeout] = PyMongoPersistableRepository(timeout_col, SagaTimeout)
    timeout_ctx = PyMongoDBContext(client)
    timeout_uow: UnitOfWork[Any] = UnitOfWork(timeout_ctx)
    # TimeoutManager gets its own repo + context + UoW so that it never shares
    # the mutable _session field with SagaManager's timeout_repo.  Concurrent
    # repo.session = ctx.session from two threads overwrites each other.
    tm_timeout_repo: PyMongoPersistableRepository[UUID, SagaTimeout] = PyMongoPersistableRepository(timeout_col, SagaTimeout)
    tm_timeout_ctx = PyMongoDBContext(client)
    tm_timeout_uow: UnitOfWork[Any] = UnitOfWork(tm_timeout_ctx)

    def cleanup() -> None:
        client.drop_database(f"saga_test_{prefix}")

    return dict(
        seat_repo=seat_repo, seat_uow=seat_uow,
        saga_repo=saga_repo, saga_uow=saga_uow,
        timeout_repo=timeout_repo, timeout_uow=timeout_uow,
        tm_timeout_repo=tm_timeout_repo, tm_timeout_uow=tm_timeout_uow,
        cleanup=cleanup,
    )


# --- Redis persistence helpers --------------------------------------------


def _redis_context(client: Redis, prefix: str, publisher: IEventPublisher[Any]) -> dict[str, Any]:  # type: ignore[type-arg]
    """Return repo/UoW stacks for one test using Redis persistence."""
    seat_repo: RedisRepository[UUID, FlightSeat] = RedisRepository(client, FlightSeat, f"{prefix}:seats")  # type: ignore[arg-type]
    seat_ctx = RedisDBContext(client)
    seat_uow: UnitOfWork[Any] = UnitOfWork(seat_ctx, event_publisher=publisher)

    saga_repo: RedisPersistableRepository[UUID, FlightBookingData] = RedisPersistableRepository(client, FlightBookingData, f"{prefix}:saga")  # type: ignore[arg-type]
    saga_ctx = RedisDBContext(client)
    saga_uow: UnitOfWork[Any] = UnitOfWork(saga_ctx)

    timeout_repo: RedisPersistableRepository[UUID, SagaTimeout] = RedisPersistableRepository(client, SagaTimeout, f"{prefix}:timeout")
    timeout_ctx = RedisDBContext(client)
    timeout_uow: UnitOfWork[Any] = UnitOfWork(timeout_ctx)
    # Separate repo + context + UoW for TimeoutManager — same race condition
    # as MongoDB: concurrent repo.session assignments from TM and SagaManager
    # threads corrupt each other's pipeline/session reference.
    tm_timeout_repo: RedisPersistableRepository[UUID, SagaTimeout] = RedisPersistableRepository(client, SagaTimeout, f"{prefix}:timeout")
    tm_timeout_ctx = RedisDBContext(client)
    tm_timeout_uow: UnitOfWork[Any] = UnitOfWork(tm_timeout_ctx)

    def cleanup() -> None:
        for key in client.scan_iter(f"{prefix}:*"):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            client.delete(key)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

    return dict(
        seat_repo=seat_repo, seat_uow=seat_uow,
        saga_repo=saga_repo, saga_uow=saga_uow,
        timeout_repo=timeout_repo, timeout_uow=timeout_uow,
        tm_timeout_repo=tm_timeout_repo, tm_timeout_uow=tm_timeout_uow,
        cleanup=cleanup,
    )


# --- RabbitMQ event helpers -----------------------------------------------


def _rabbitmq_connect(params: pika.ConnectionParameters) -> tuple[pika.BlockingConnection, BlockingChannel]:
    """Create a BlockingConnection + channel, retrying on transient connection resets.

    RabbitMQ briefly rejects new connections right after the readiness check
    passes (container not yet fully warm for concurrent connections).  Five
    attempts with 1 s gaps give up to 4 s of retry time.
    """
    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(5):
        try:
            conn = pika.BlockingConnection(params)
            ch = conn.channel()
            assert isinstance(ch, BlockingChannel)
            return conn, ch
        except Exception as exc:
            last_exc = exc
            if attempt < 4:
                time.sleep(1.0)
    raise last_exc


def _rabbitmq_event_stack(
    params: pika.ConnectionParameters,
    exchange: str,
    booking_queue: str,
    payment_queue: str,
) -> dict[str, Any]:
    pub_conn, pub_ch = _rabbitmq_connect(params)
    publisher = RabbitMQEventPublisher(pub_ch, exchange=exchange)

    booking_conn, booking_ch = _rabbitmq_connect(params)
    booking_sub = RabbitMQEventSubscriber(booking_ch, exchange=exchange, queue=booking_queue)

    payment_conn, payment_ch = _rabbitmq_connect(params)
    payment_sub = RabbitMQEventSubscriber(payment_ch, exchange=exchange, queue=payment_queue)

    def cleanup() -> None:
        for conn in (pub_conn, booking_conn, payment_conn):
            try:
                conn.close()
            except Exception:
                pass

    return dict(publisher=publisher, booking_sub=booking_sub, payment_sub=payment_sub, cleanup=cleanup)


# --- Kafka event helpers --------------------------------------------------


def _kafka_event_stack(bootstrap: str, topic_prefix: str) -> dict[str, Any]:
    producer = Producer({"bootstrap.servers": bootstrap})
    publisher = KafkaEventPublisher(producer, topic_prefix=topic_prefix)

    def _consumer(group_id: str) -> Consumer:
        return Consumer({
            "bootstrap.servers": bootstrap,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": "false",
        })

    booking_sub = KafkaEventSubscriber(_consumer(f"{topic_prefix}.booking"), topic_prefix=topic_prefix)
    payment_sub = KafkaEventSubscriber(_consumer(f"{topic_prefix}.payment"), topic_prefix=topic_prefix)

    return dict(publisher=publisher, booking_sub=booking_sub, payment_sub=payment_sub, cleanup=lambda: None)


# --- Redis Streams event helpers ------------------------------------------


def _redis_event_stack(client: Redis, stream_prefix: str) -> dict[str, Any]:  # type: ignore[type-arg]
    publisher = RedisEventPublisher(client, stream_prefix=stream_prefix)
    booking_sub = RedisEventSubscriber(
        client,
        stream_prefix=stream_prefix,
        group=f"{stream_prefix}.booking",
        claim_idle_ms=200,
    )
    payment_sub = RedisEventSubscriber(
        client,
        stream_prefix=stream_prefix,
        group=f"{stream_prefix}.payment",
        claim_idle_ms=200,
    )
    return dict(publisher=publisher, booking_sub=booking_sub, payment_sub=payment_sub, cleanup=lambda: None)


# ===========================================================================
# Scenario base class
# ===========================================================================


class SagaIntegrationBase:
    """Four scenario tests.  Subclasses must supply a ``ctx`` fixture."""

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _fresh_prefix() -> str:
        return uuid.uuid4().hex[:10]

    # ── scenarios ──────────────────────────────────────────────────────────

    def test_happy_path(self, ctx: Any) -> None:
        """Reserve → payment approved → booking confirmed."""
        ctx.start(include_payment=True)
        seat_id = ctx.add_seat()
        ctx.reserve(seat_id, flight_id="AA101", passenger="Alice",
                    card_token="4242-4242-4242-4242", amount=399.99)
        assert ctx.wait(timeout=15.0), "Happy path: saga did not complete within 15 s"

    def test_failure_path(self, ctx: Any) -> None:
        """Reserve → payment declined → reservation cancelled."""
        ctx.start(include_payment=True)
        seat_id = ctx.add_seat()
        ctx.reserve(seat_id, flight_id="AA202", passenger="Bob",
                    card_token="0000-0000-0000-0000", amount=399.99)
        assert ctx.wait(timeout=15.0), "Failure path: saga did not complete within 15 s"

    def test_timeout(self, ctx: Any) -> None:
        """Reserve → payment service absent → timeout fires → cancelled."""
        ctx.start(include_payment=False)
        seat_id = ctx.add_seat()
        ctx.reserve(seat_id, flight_id="AA303", passenger="Carol",
                    card_token="4242-4242-4242-4242", amount=249.00)
        assert ctx.wait(timeout=15.0), "Timeout: saga did not complete within 15 s"

    def test_not_found(self, ctx: Any) -> None:
        """PaymentReceived with no prior reservation → saga never completes."""
        ctx.start(include_payment=False)
        # Publish PaymentReceived directly — no FlightReserved was ever sent,
        # so no saga instance exists.  SagaManager raises SagaNotFoundError inside
        # the subscriber thread/process; the message is nacked and requeued.  The
        # saga completion event must never fire.
        ctx.publisher.publish([PaymentReceived(flight_id="AA404-orphan", amount=0.0)])
        time.sleep(3.0)
        assert not ctx.completed.is_set(), "No saga should have completed for an orphan PaymentReceived"


# ===========================================================================
# Concrete test classes — 2 persistence × 3 event providers = 6 combinations
# ===========================================================================

# ---------------------------------------------------------------------------
# MongoDB + RabbitMQ
# ---------------------------------------------------------------------------


class TestSagaMongoRabbitMQ(SagaIntegrationBase):
    @pytest.fixture
    def ctx(
        self,
        mongo_client: MongoClient[Any],
        rabbitmq_params: pika.ConnectionParameters,
    ) -> Iterator[SagaTestContext]:
        p = self._fresh_prefix()
        exchange = f"saga.test.{p}"
        ev = _rabbitmq_event_stack(rabbitmq_params, exchange, f"booking.{p}", f"payment.{p}")
        persist = _mongo_context(mongo_client, p, ev["publisher"])
        ctx = _build_context(**{k: v for k, v in {**persist, **ev}.items()
                                if k not in ("cleanup",)})
        try:
            yield ctx
        finally:
            ctx.stop()
            ev["cleanup"]()
            persist["cleanup"]()


# ---------------------------------------------------------------------------
# MongoDB + Kafka
# ---------------------------------------------------------------------------


class TestSagaMongoKafka(SagaIntegrationBase):
    @pytest.fixture
    def ctx(
        self,
        mongo_client: MongoClient[Any],
        kafka_bootstrap: str,
    ) -> Iterator[SagaTestContext]:
        p = self._fresh_prefix()
        ev = _kafka_event_stack(kafka_bootstrap, f"saga.{p}")
        persist = _mongo_context(mongo_client, p, ev["publisher"])
        ctx = _build_context(**{k: v for k, v in {**persist, **ev}.items()
                                if k not in ("cleanup",)})
        try:
            yield ctx
        finally:
            ctx.stop()
            persist["cleanup"]()


# ---------------------------------------------------------------------------
# MongoDB + Redis Streams
# ---------------------------------------------------------------------------


class TestSagaMongoRedisEvents(SagaIntegrationBase):
    @pytest.fixture
    def ctx(
        self,
        mongo_client: MongoClient[Any],
        redis_events_client: Redis,  # type: ignore[type-arg]
    ) -> Iterator[SagaTestContext]:
        p = self._fresh_prefix()
        ev = _redis_event_stack(redis_events_client, f"saga.{p}")
        persist = _mongo_context(mongo_client, p, ev["publisher"])
        ctx = _build_context(**{k: v for k, v in {**persist, **ev}.items()
                                if k not in ("cleanup",)})
        try:
            yield ctx
        finally:
            ctx.stop()
            persist["cleanup"]()


# ---------------------------------------------------------------------------
# Redis (persistence) + RabbitMQ
# ---------------------------------------------------------------------------


class TestSagaRedisRabbitMQ(SagaIntegrationBase):
    @pytest.fixture
    def ctx(
        self,
        redis_persist_client: Redis,  # type: ignore[type-arg]
        rabbitmq_params: pika.ConnectionParameters,
    ) -> Iterator[SagaTestContext]:
        p = self._fresh_prefix()
        exchange = f"saga.test.{p}"
        ev = _rabbitmq_event_stack(rabbitmq_params, exchange, f"booking.{p}", f"payment.{p}")
        persist = _redis_context(redis_persist_client, p, ev["publisher"])
        ctx = _build_context(**{k: v for k, v in {**persist, **ev}.items()
                                if k not in ("cleanup",)})
        try:
            yield ctx
        finally:
            ctx.stop()
            ev["cleanup"]()
            persist["cleanup"]()


# ---------------------------------------------------------------------------
# Redis (persistence) + Kafka
# ---------------------------------------------------------------------------


class TestSagaRedisKafka(SagaIntegrationBase):
    @pytest.fixture
    def ctx(
        self,
        redis_persist_client: Redis,  # type: ignore[type-arg]
        kafka_bootstrap: str,
    ) -> Iterator[SagaTestContext]:
        p = self._fresh_prefix()
        ev = _kafka_event_stack(kafka_bootstrap, f"saga.{p}")
        persist = _redis_context(redis_persist_client, p, ev["publisher"])
        ctx = _build_context(**{k: v for k, v in {**persist, **ev}.items()
                                if k not in ("cleanup",)})
        try:
            yield ctx
        finally:
            ctx.stop()
            persist["cleanup"]()


# ---------------------------------------------------------------------------
# Redis (persistence) + Redis Streams (events)
# ---------------------------------------------------------------------------


class TestSagaRedisRedis(SagaIntegrationBase):
    @pytest.fixture
    def ctx(
        self,
        redis_persist_client: Redis,  # type: ignore[type-arg]
        redis_events_client: Redis,  # type: ignore[type-arg]
    ) -> Iterator[SagaTestContext]:
        p = self._fresh_prefix()
        ev = _redis_event_stack(redis_events_client, f"saga.{p}")
        persist = _redis_context(redis_persist_client, p, ev["publisher"])
        ctx = _build_context(**{k: v for k, v in {**persist, **ev}.items()
                                if k not in ("cleanup",)})
        try:
            yield ctx
        finally:
            ctx.stop()
            persist["cleanup"]()


# ===========================================================================
# Process-based saga tests — same 6 combinations using ProcessBackgroundTaskRunner
# ===========================================================================
#
# Each subprocess task creates ALL its own connections from scratch (host/port
# params only, not inherited objects) so that fork-unsafe libraries (pymongo,
# pika, confluent-kafka) get a fresh state inside the child process.
#
# The booking subscriber and TimeoutManager run together in one subprocess
# (they must share the same SagaManager object so that TM can dispatch to it).
# The payment service runs in a separate subprocess.
#
# The completion signal is a multiprocessing.Event so that set() in the child
# is visible to the parent's wait() call.
# ---------------------------------------------------------------------------


class _SubprocessProvider(IBackgroundTasks):
    """Wraps a plain subprocess callable as IBackgroundTasks (no external cleanup needed)."""

    def __init__(self, fn: Callable[[], Any]) -> None:
        self._fn = fn

    def tasks(self) -> list[Task]:
        return [self._fn]  # type: ignore[list-item]

    def cleanup(self) -> None:
        pass


@dataclass
class ProcessSagaTestContext:
    """Parallel to SagaTestContext but for process-based runners.

    booking_task combines the event subscriber + TimeoutManager (in a thread).
    payment_task runs the payment service in a separate process.
    completed is a multiprocessing.Event shared between parent and child.
    """

    seat_repo: Any
    seat_uow: UnitOfWork[Any]
    publisher: IEventPublisher[Any]
    runner: ProcessBackgroundTaskRunner
    completed: Any  # multiprocessing.synchronize.Event
    booking_task: Callable[[], Any]
    payment_task: Callable[[], Any]

    def start(self, *, include_payment: bool = True) -> None:
        providers: list[IBackgroundTasks] = [_SubprocessProvider(self.booking_task)]
        if include_payment:
            providers.append(_SubprocessProvider(self.payment_task))
        self.runner.start(*providers)
        time.sleep(1.0)  # subprocess startup takes longer than thread startup

    def stop(self) -> None:
        self.runner.stop()

    def add_seat(self) -> UUID:
        seat = FlightSeat()
        with self.seat_uow(self.seat_repo, auto_commit=True):
            self.seat_repo.save(seat)
        return seat.id.value  # type: ignore[no-any-return]

    def reserve(
        self,
        seat_id: UUID,
        *,
        flight_id: str,
        passenger: str,
        card_token: str,
        amount: float,
    ) -> None:
        with self.seat_uow(self.seat_repo, auto_commit=True):
            seat = self.seat_repo.get_one(seat_id)
            seat.reserve(
                flight_id=flight_id,
                passenger=passenger,
                card_token=card_token,
                amount=amount,
            )
            self.seat_repo.update(seat)

    def wait(self, timeout: float = 15.0) -> bool:
        return self.completed.wait(timeout=timeout)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Subprocess-safe helpers: build SagaManager + TimeoutManager from params
# ---------------------------------------------------------------------------


def _saga_manager_from_mongo(
    host: str,
    port: int,
    db_name: str,
    publisher: IEventPublisher[Any],
    completed: Any,
) -> SagaManager[FlightBookingData]:  # type: ignore[type-arg]
    """Create a SagaManager with a fresh MongoClient — safe to call after fork."""
    client: MongoClient[Any] = MongoClient(
        host=host, port=port, directConnection=True, uuidRepresentation="standard"
    )
    db = client[db_name]
    db["seats"].create_index("id", unique=True)
    seat_repo: PyMongoRepository[UUID, FlightSeat] = PyMongoRepository(db["seats"], FlightSeat)  # type: ignore[arg-type]
    FlightBookingSaga.seat_repo = seat_repo  # type: ignore[assignment]
    saga_repo: PyMongoPersistableRepository[UUID, FlightBookingData] = PyMongoPersistableRepository(
        db["saga_data"], FlightBookingData  # type: ignore[arg-type]
    )
    timeout_repo: PyMongoPersistableRepository[UUID, SagaTimeout] = PyMongoPersistableRepository(
        db["saga_timeouts"], SagaTimeout
    )
    return SagaManager(
        FlightBookingSaga,  # type: ignore[arg-type]
        saga_repo,
        publisher=publisher,
        timeout_repo=timeout_repo,
        uow=UnitOfWork(PyMongoDBContext(client)),
        timeout_uow=UnitOfWork(PyMongoDBContext(client)),
        on_complete=lambda _: completed.set(),
    )


def _timeout_manager_from_mongo(host: str, port: int, db_name: str) -> TimeoutManager:
    """Create a TimeoutManager with its own MongoClient — safe to call after fork."""
    client: MongoClient[Any] = MongoClient(
        host=host, port=port, directConnection=True, uuidRepresentation="standard"
    )
    db = client[db_name]
    tm_repo: PyMongoPersistableRepository[UUID, SagaTimeout] = PyMongoPersistableRepository(
        db["saga_timeouts"], SagaTimeout
    )
    return TimeoutManager(tm_repo, poll_interval=0.05, uow=UnitOfWork(PyMongoDBContext(client)))


def _saga_manager_from_redis(
    host: str,
    port: int,
    prefix: str,
    publisher: IEventPublisher[Any],
    completed: Any,
) -> SagaManager[FlightBookingData]:  # type: ignore[type-arg]
    """Create a SagaManager with a fresh Redis client — safe to call after fork."""
    client: Redis = Redis(host=host, port=port)  # type: ignore[type-arg]
    seat_repo: RedisRepository[UUID, FlightSeat] = RedisRepository(client, FlightSeat, f"{prefix}:seats")  # type: ignore[arg-type]
    FlightBookingSaga.seat_repo = seat_repo  # type: ignore[assignment]
    saga_repo: RedisPersistableRepository[UUID, FlightBookingData] = RedisPersistableRepository(
        client, FlightBookingData, f"{prefix}:saga"  # type: ignore[arg-type]
    )
    timeout_repo: RedisPersistableRepository[UUID, SagaTimeout] = RedisPersistableRepository(
        client, SagaTimeout, f"{prefix}:timeout"
    )
    return SagaManager(
        FlightBookingSaga,  # type: ignore[arg-type]
        saga_repo,
        publisher=publisher,
        timeout_repo=timeout_repo,
        uow=UnitOfWork(RedisDBContext(client)),
        timeout_uow=UnitOfWork(RedisDBContext(client)),
        on_complete=lambda _: completed.set(),
    )


def _timeout_manager_from_redis(host: str, port: int, prefix: str) -> TimeoutManager:
    """Create a TimeoutManager with its own Redis client — safe to call after fork."""
    client: Redis = Redis(host=host, port=port)  # type: ignore[type-arg]
    tm_repo: RedisPersistableRepository[UUID, SagaTimeout] = RedisPersistableRepository(
        client, SagaTimeout, f"{prefix}:timeout"
    )
    return TimeoutManager(tm_repo, poll_interval=0.05, uow=UnitOfWork(RedisDBContext(client)))


# ---------------------------------------------------------------------------
# Generic subprocess task builders
# ---------------------------------------------------------------------------


def _booking_tm_subprocess(
    subscriber_factory: Callable[[], Any],
    publisher_factory: Callable[[], IEventPublisher[Any]],
    manager_factory: Callable[[IEventPublisher[Any]], SagaManager[Any]],
    tm_factory: Callable[[], TimeoutManager],
) -> Callable[[], Any]:
    """Return a callable that — when run in a subprocess — wires and starts booking + TM."""
    def run() -> Any:
        sub = subscriber_factory()
        pub = publisher_factory()
        manager = manager_factory(pub)
        tm = tm_factory()
        tm.register(manager)
        manager.subscribe_to(sub)
        threading.Thread(target=tm.start, daemon=True).start()
        sub.tasks()[0]()
    return run


def _payment_subprocess(
    subscriber_factory: Callable[[], Any],
    publisher_factory: Callable[[], IEventPublisher[Any]],
) -> Callable[[], Any]:
    """Return a callable that — when run in a subprocess — wires and starts payment service."""
    def run() -> Any:
        sub = subscriber_factory()
        pub = publisher_factory()
        payment_service = PaymentService(pub)
        sub.subscribe(payment_service)
        sub.tasks()[0]()
    return run


# ---------------------------------------------------------------------------
# Per-combination process context builders
# ---------------------------------------------------------------------------


def _process_ctx_mongo_rmq(
    mongo_client: MongoClient[Any],
    rmq_params: pika.ConnectionParameters,
    prefix: str,
) -> tuple[ProcessSagaTestContext, Callable[[], None]]:
    rmq_host = str(rmq_params.host)
    rmq_port = int(rmq_params.port or 5672)
    exchange = f"saga.proc.{prefix}"
    booking_q = f"booking.proc.{prefix}"
    payment_q = f"payment.proc.{prefix}"

    mongo_node = next(iter(mongo_client.nodes))
    mongo_host, mongo_port = str(mongo_node[0]), int(mongo_node[1] or 27017)
    db_name = f"saga_proc_{prefix}"
    mongo_client[db_name]["seats"].create_index("id", unique=True)

    seat_repo: PyMongoRepository[UUID, FlightSeat] = PyMongoRepository(
        mongo_client[db_name]["seats"], FlightSeat  # type: ignore[arg-type]
    )
    pub_conn, pub_ch = _rabbitmq_connect(rmq_params)
    main_publisher = RabbitMQEventPublisher(pub_ch, exchange=exchange)
    seat_uow: UnitOfWork[Any] = UnitOfWork(PyMongoDBContext(mongo_client), event_publisher=main_publisher)

    completed: Any = multiprocessing.Event()

    booking_task = _booking_tm_subprocess(
        subscriber_factory=lambda: RabbitMQEventSubscriber(
            _rabbitmq_connect(pika.ConnectionParameters(host=rmq_host, port=rmq_port))[1],
            exchange=exchange, queue=booking_q,
        ),
        publisher_factory=lambda: RabbitMQEventPublisher(
            _rabbitmq_connect(pika.ConnectionParameters(host=rmq_host, port=rmq_port))[1],
            exchange=exchange,
        ),
        manager_factory=lambda pub: _saga_manager_from_mongo(mongo_host, mongo_port, db_name, pub, completed),
        tm_factory=lambda: _timeout_manager_from_mongo(mongo_host, mongo_port, db_name),
    )
    payment_task = _payment_subprocess(
        subscriber_factory=lambda: RabbitMQEventSubscriber(
            _rabbitmq_connect(pika.ConnectionParameters(host=rmq_host, port=rmq_port))[1],
            exchange=exchange, queue=payment_q,
        ),
        publisher_factory=lambda: RabbitMQEventPublisher(
            _rabbitmq_connect(pika.ConnectionParameters(host=rmq_host, port=rmq_port))[1],
            exchange=exchange,
        ),
    )

    def cleanup() -> None:
        try:
            pub_conn.close()
        except Exception:
            pass
        mongo_client.drop_database(db_name)

    return (
        ProcessSagaTestContext(
            seat_repo=seat_repo, seat_uow=seat_uow, publisher=main_publisher,
            runner=ProcessBackgroundTaskRunner(), completed=completed,
            booking_task=booking_task, payment_task=payment_task,
        ),
        cleanup,
    )


def _process_ctx_mongo_kafka(
    mongo_client: MongoClient[Any],
    kafka_bootstrap: str,
    prefix: str,
) -> tuple[ProcessSagaTestContext, Callable[[], None]]:
    topic_prefix = f"saga.proc.{prefix}"

    mongo_node = next(iter(mongo_client.nodes))
    mongo_host, mongo_port = str(mongo_node[0]), int(mongo_node[1] or 27017)
    db_name = f"saga_proc_{prefix}"
    mongo_client[db_name]["seats"].create_index("id", unique=True)

    seat_repo: PyMongoRepository[UUID, FlightSeat] = PyMongoRepository(
        mongo_client[db_name]["seats"], FlightSeat  # type: ignore[arg-type]
    )
    main_publisher = KafkaEventPublisher(
        Producer({"bootstrap.servers": kafka_bootstrap}), topic_prefix=topic_prefix
    )
    seat_uow: UnitOfWork[Any] = UnitOfWork(PyMongoDBContext(mongo_client), event_publisher=main_publisher)

    completed: Any = multiprocessing.Event()

    def _kafka_consumer(group: str) -> Consumer:
        return Consumer({
            "bootstrap.servers": kafka_bootstrap,
            "group.id": group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": "false",
        })

    booking_task = _booking_tm_subprocess(
        subscriber_factory=lambda: KafkaEventSubscriber(
            _kafka_consumer(f"{topic_prefix}.proc.booking"), topic_prefix=topic_prefix
        ),
        publisher_factory=lambda: KafkaEventPublisher(
            Producer({"bootstrap.servers": kafka_bootstrap}), topic_prefix=topic_prefix
        ),
        manager_factory=lambda pub: _saga_manager_from_mongo(mongo_host, mongo_port, db_name, pub, completed),
        tm_factory=lambda: _timeout_manager_from_mongo(mongo_host, mongo_port, db_name),
    )
    payment_task = _payment_subprocess(
        subscriber_factory=lambda: KafkaEventSubscriber(
            _kafka_consumer(f"{topic_prefix}.proc.payment"), topic_prefix=topic_prefix
        ),
        publisher_factory=lambda: KafkaEventPublisher(
            Producer({"bootstrap.servers": kafka_bootstrap}), topic_prefix=topic_prefix
        ),
    )

    def cleanup() -> None:
        mongo_client.drop_database(db_name)

    return (
        ProcessSagaTestContext(
            seat_repo=seat_repo, seat_uow=seat_uow, publisher=main_publisher,
            runner=ProcessBackgroundTaskRunner(), completed=completed,
            booking_task=booking_task, payment_task=payment_task,
        ),
        cleanup,
    )


def _process_ctx_mongo_redis_stream(
    mongo_client: MongoClient[Any],
    redis_events_client: Redis,  # type: ignore[type-arg]
    prefix: str,
) -> tuple[ProcessSagaTestContext, Callable[[], None]]:
    stream_prefix = f"saga.proc.{prefix}"

    mongo_node = next(iter(mongo_client.nodes))
    mongo_host, mongo_port = str(mongo_node[0]), int(mongo_node[1] or 27017)
    db_name = f"saga_proc_{prefix}"
    mongo_client[db_name]["seats"].create_index("id", unique=True)

    seat_repo: PyMongoRepository[UUID, FlightSeat] = PyMongoRepository(
        mongo_client[db_name]["seats"], FlightSeat  # type: ignore[arg-type]
    )
    ev_host: str = redis_events_client.connection_pool.connection_kwargs["host"]  # type: ignore[index]
    ev_port: int = redis_events_client.connection_pool.connection_kwargs["port"]  # type: ignore[index]
    main_publisher = RedisEventPublisher(redis_events_client, stream_prefix=stream_prefix)
    seat_uow: UnitOfWork[Any] = UnitOfWork(PyMongoDBContext(mongo_client), event_publisher=main_publisher)

    completed: Any = multiprocessing.Event()

    booking_task = _booking_tm_subprocess(
        subscriber_factory=lambda: RedisEventSubscriber(
            Redis(host=ev_host, port=ev_port), stream_prefix=stream_prefix,
            group=f"{stream_prefix}.proc.booking", claim_idle_ms=200,
        ),
        publisher_factory=lambda: RedisEventPublisher(
            Redis(host=ev_host, port=ev_port), stream_prefix=stream_prefix,
        ),
        manager_factory=lambda pub: _saga_manager_from_mongo(mongo_host, mongo_port, db_name, pub, completed),
        tm_factory=lambda: _timeout_manager_from_mongo(mongo_host, mongo_port, db_name),
    )
    payment_task = _payment_subprocess(
        subscriber_factory=lambda: RedisEventSubscriber(
            Redis(host=ev_host, port=ev_port), stream_prefix=stream_prefix,
            group=f"{stream_prefix}.proc.payment", claim_idle_ms=200,
        ),
        publisher_factory=lambda: RedisEventPublisher(
            Redis(host=ev_host, port=ev_port), stream_prefix=stream_prefix,
        ),
    )

    def cleanup() -> None:
        mongo_client.drop_database(db_name)

    return (
        ProcessSagaTestContext(
            seat_repo=seat_repo, seat_uow=seat_uow, publisher=main_publisher,
            runner=ProcessBackgroundTaskRunner(), completed=completed,
            booking_task=booking_task, payment_task=payment_task,
        ),
        cleanup,
    )


def _process_ctx_redis_rmq(
    redis_persist_client: Redis,  # type: ignore[type-arg]
    rmq_params: pika.ConnectionParameters,
    prefix: str,
) -> tuple[ProcessSagaTestContext, Callable[[], None]]:
    rmq_host = str(rmq_params.host)
    rmq_port = int(rmq_params.port or 5672)
    exchange = f"saga.proc.{prefix}"
    booking_q = f"booking.proc.{prefix}"
    payment_q = f"payment.proc.{prefix}"

    rp_host: str = redis_persist_client.connection_pool.connection_kwargs["host"]  # type: ignore[index]
    rp_port: int = redis_persist_client.connection_pool.connection_kwargs["port"]  # type: ignore[index]

    seat_repo: RedisRepository[UUID, FlightSeat] = RedisRepository(
        redis_persist_client, FlightSeat, f"{prefix}:seats"  # type: ignore[arg-type]
    )
    pub_conn, pub_ch = _rabbitmq_connect(rmq_params)
    main_publisher = RabbitMQEventPublisher(pub_ch, exchange=exchange)
    seat_uow: UnitOfWork[Any] = UnitOfWork(RedisDBContext(redis_persist_client), event_publisher=main_publisher)

    completed: Any = multiprocessing.Event()

    booking_task = _booking_tm_subprocess(
        subscriber_factory=lambda: RabbitMQEventSubscriber(
            _rabbitmq_connect(pika.ConnectionParameters(host=rmq_host, port=rmq_port))[1],
            exchange=exchange, queue=booking_q,
        ),
        publisher_factory=lambda: RabbitMQEventPublisher(
            _rabbitmq_connect(pika.ConnectionParameters(host=rmq_host, port=rmq_port))[1],
            exchange=exchange,
        ),
        manager_factory=lambda pub: _saga_manager_from_redis(rp_host, rp_port, prefix, pub, completed),
        tm_factory=lambda: _timeout_manager_from_redis(rp_host, rp_port, prefix),
    )
    payment_task = _payment_subprocess(
        subscriber_factory=lambda: RabbitMQEventSubscriber(
            _rabbitmq_connect(pika.ConnectionParameters(host=rmq_host, port=rmq_port))[1],
            exchange=exchange, queue=payment_q,
        ),
        publisher_factory=lambda: RabbitMQEventPublisher(
            _rabbitmq_connect(pika.ConnectionParameters(host=rmq_host, port=rmq_port))[1],
            exchange=exchange,
        ),
    )

    def cleanup() -> None:
        try:
            pub_conn.close()
        except Exception:
            pass
        for key in redis_persist_client.scan_iter(f"{prefix}:*"):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            redis_persist_client.delete(key)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

    return (
        ProcessSagaTestContext(
            seat_repo=seat_repo, seat_uow=seat_uow, publisher=main_publisher,
            runner=ProcessBackgroundTaskRunner(), completed=completed,
            booking_task=booking_task, payment_task=payment_task,
        ),
        cleanup,
    )


def _process_ctx_redis_kafka(
    redis_persist_client: Redis,  # type: ignore[type-arg]
    kafka_bootstrap: str,
    prefix: str,
) -> tuple[ProcessSagaTestContext, Callable[[], None]]:
    topic_prefix = f"saga.proc.{prefix}"
    rp_host: str = redis_persist_client.connection_pool.connection_kwargs["host"]  # type: ignore[index]
    rp_port: int = redis_persist_client.connection_pool.connection_kwargs["port"]  # type: ignore[index]

    seat_repo: RedisRepository[UUID, FlightSeat] = RedisRepository(
        redis_persist_client, FlightSeat, f"{prefix}:seats"  # type: ignore[arg-type]
    )
    main_publisher = KafkaEventPublisher(
        Producer({"bootstrap.servers": kafka_bootstrap}), topic_prefix=topic_prefix
    )
    seat_uow: UnitOfWork[Any] = UnitOfWork(RedisDBContext(redis_persist_client), event_publisher=main_publisher)

    completed: Any = multiprocessing.Event()

    def _kafka_consumer(group: str) -> Consumer:
        return Consumer({
            "bootstrap.servers": kafka_bootstrap,
            "group.id": group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": "false",
        })

    booking_task = _booking_tm_subprocess(
        subscriber_factory=lambda: KafkaEventSubscriber(
            _kafka_consumer(f"{topic_prefix}.proc.booking"), topic_prefix=topic_prefix
        ),
        publisher_factory=lambda: KafkaEventPublisher(
            Producer({"bootstrap.servers": kafka_bootstrap}), topic_prefix=topic_prefix
        ),
        manager_factory=lambda pub: _saga_manager_from_redis(rp_host, rp_port, prefix, pub, completed),
        tm_factory=lambda: _timeout_manager_from_redis(rp_host, rp_port, prefix),
    )
    payment_task = _payment_subprocess(
        subscriber_factory=lambda: KafkaEventSubscriber(
            _kafka_consumer(f"{topic_prefix}.proc.payment"), topic_prefix=topic_prefix
        ),
        publisher_factory=lambda: KafkaEventPublisher(
            Producer({"bootstrap.servers": kafka_bootstrap}), topic_prefix=topic_prefix
        ),
    )

    def cleanup() -> None:
        for key in redis_persist_client.scan_iter(f"{prefix}:*"):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            redis_persist_client.delete(key)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

    return (
        ProcessSagaTestContext(
            seat_repo=seat_repo, seat_uow=seat_uow, publisher=main_publisher,
            runner=ProcessBackgroundTaskRunner(), completed=completed,
            booking_task=booking_task, payment_task=payment_task,
        ),
        cleanup,
    )


def _process_ctx_redis_redis(
    redis_persist_client: Redis,  # type: ignore[type-arg]
    redis_events_client: Redis,  # type: ignore[type-arg]
    prefix: str,
) -> tuple[ProcessSagaTestContext, Callable[[], None]]:
    stream_prefix = f"saga.proc.{prefix}"
    rp_host: str = redis_persist_client.connection_pool.connection_kwargs["host"]  # type: ignore[index]
    rp_port: int = redis_persist_client.connection_pool.connection_kwargs["port"]  # type: ignore[index]
    ev_host: str = redis_events_client.connection_pool.connection_kwargs["host"]  # type: ignore[index]
    ev_port: int = redis_events_client.connection_pool.connection_kwargs["port"]  # type: ignore[index]

    seat_repo: RedisRepository[UUID, FlightSeat] = RedisRepository(
        redis_persist_client, FlightSeat, f"{prefix}:seats"  # type: ignore[arg-type]
    )
    main_publisher = RedisEventPublisher(redis_events_client, stream_prefix=stream_prefix)
    seat_uow: UnitOfWork[Any] = UnitOfWork(RedisDBContext(redis_persist_client), event_publisher=main_publisher)

    completed: Any = multiprocessing.Event()

    booking_task = _booking_tm_subprocess(
        subscriber_factory=lambda: RedisEventSubscriber(
            Redis(host=ev_host, port=ev_port), stream_prefix=stream_prefix,
            group=f"{stream_prefix}.proc.booking", claim_idle_ms=200,
        ),
        publisher_factory=lambda: RedisEventPublisher(
            Redis(host=ev_host, port=ev_port), stream_prefix=stream_prefix,
        ),
        manager_factory=lambda pub: _saga_manager_from_redis(rp_host, rp_port, prefix, pub, completed),
        tm_factory=lambda: _timeout_manager_from_redis(rp_host, rp_port, prefix),
    )
    payment_task = _payment_subprocess(
        subscriber_factory=lambda: RedisEventSubscriber(
            Redis(host=ev_host, port=ev_port), stream_prefix=stream_prefix,
            group=f"{stream_prefix}.proc.payment", claim_idle_ms=200,
        ),
        publisher_factory=lambda: RedisEventPublisher(
            Redis(host=ev_host, port=ev_port), stream_prefix=stream_prefix,
        ),
    )

    def cleanup() -> None:
        for key in redis_persist_client.scan_iter(f"{prefix}:*"):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            redis_persist_client.delete(key)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

    return (
        ProcessSagaTestContext(
            seat_repo=seat_repo, seat_uow=seat_uow, publisher=main_publisher,
            runner=ProcessBackgroundTaskRunner(), completed=completed,
            booking_task=booking_task, payment_task=payment_task,
        ),
        cleanup,
    )


# ===========================================================================
# Concrete process test classes — same 6 combinations, ProcessBackgroundTaskRunner
# ===========================================================================


class TestSagaMongoRabbitMQProcess(SagaIntegrationBase):
    @pytest.fixture
    def ctx(
        self,
        mongo_client: MongoClient[Any],
        rabbitmq_params: pika.ConnectionParameters,
    ) -> Iterator[ProcessSagaTestContext]:
        p = self._fresh_prefix()
        proc_ctx, cleanup = _process_ctx_mongo_rmq(mongo_client, rabbitmq_params, p)
        try:
            yield proc_ctx
        finally:
            proc_ctx.stop()
            cleanup()


class TestSagaMongoKafkaProcess(SagaIntegrationBase):
    @pytest.fixture
    def ctx(
        self,
        mongo_client: MongoClient[Any],
        kafka_bootstrap: str,
    ) -> Iterator[ProcessSagaTestContext]:
        p = self._fresh_prefix()
        proc_ctx, cleanup = _process_ctx_mongo_kafka(mongo_client, kafka_bootstrap, p)
        try:
            yield proc_ctx
        finally:
            proc_ctx.stop()
            cleanup()


class TestSagaMongoRedisEventsProcess(SagaIntegrationBase):
    @pytest.fixture
    def ctx(
        self,
        mongo_client: MongoClient[Any],
        redis_events_client: Redis,  # type: ignore[type-arg]
    ) -> Iterator[ProcessSagaTestContext]:
        p = self._fresh_prefix()
        proc_ctx, cleanup = _process_ctx_mongo_redis_stream(mongo_client, redis_events_client, p)
        try:
            yield proc_ctx
        finally:
            proc_ctx.stop()
            cleanup()


class TestSagaRedisRabbitMQProcess(SagaIntegrationBase):
    @pytest.fixture
    def ctx(
        self,
        redis_persist_client: Redis,  # type: ignore[type-arg]
        rabbitmq_params: pika.ConnectionParameters,
    ) -> Iterator[ProcessSagaTestContext]:
        p = self._fresh_prefix()
        proc_ctx, cleanup = _process_ctx_redis_rmq(redis_persist_client, rabbitmq_params, p)
        try:
            yield proc_ctx
        finally:
            proc_ctx.stop()
            cleanup()


class TestSagaRedisKafkaProcess(SagaIntegrationBase):
    @pytest.fixture
    def ctx(
        self,
        redis_persist_client: Redis,  # type: ignore[type-arg]
        kafka_bootstrap: str,
    ) -> Iterator[ProcessSagaTestContext]:
        p = self._fresh_prefix()
        proc_ctx, cleanup = _process_ctx_redis_kafka(redis_persist_client, kafka_bootstrap, p)
        try:
            yield proc_ctx
        finally:
            proc_ctx.stop()
            cleanup()


class TestSagaRedisRedisProcess(SagaIntegrationBase):
    @pytest.fixture
    def ctx(
        self,
        redis_persist_client: Redis,  # type: ignore[type-arg]
        redis_events_client: Redis,  # type: ignore[type-arg]
    ) -> Iterator[ProcessSagaTestContext]:
        p = self._fresh_prefix()
        proc_ctx, cleanup = _process_ctx_redis_redis(redis_persist_client, redis_events_client, p)
        try:
            yield proc_ctx
        finally:
            proc_ctx.stop()
            cleanup()
