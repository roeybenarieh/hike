"""Tests for the saga process manager pattern."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, ClassVar, Iterable, cast
from unittest.mock import create_autospec
from uuid import UUID, uuid4

import pytest

from hike.events.integration_event import IntegrationEvent
from hike.events.interfaces.publisher import IExternalEventPublisher
from hike.events.interfaces.background_task import Task
from hike.events.saga import (
    Saga,
    SagaContext,
    SagaData,
    SagaMapper,
    SagaManager,
    SagaNotFoundError,
    SagaTimeout,
    TimeoutManager,
    compensates,
    handles,
    started_by,
    timeout_handler,
)
from hike.persistence.providers.in_memory import InMemoryPersistableRepository
from hike.persistence.repository import LockConflictError


# ─── Test helpers ─────────────────────────────────────────────────────────────


class _NullPublisher(IExternalEventPublisher[IntegrationEvent]):
    """Minimal IExternalEventPublisher for use in tests that don't care about publish output."""

    def publish(self, events: Iterable[IntegrationEvent]) -> None: ...

    def tasks(self) -> list[Task]:
        return []

    def cleanup(self) -> None: ...


# ─── Shared domain types ──────────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True, eq=False)
class OrderPlaced(IntegrationEvent):
    order_id: str
    source: ClassVar[str] = "//test-service"
    version: int = 1


@dataclass(frozen=True, kw_only=True, eq=False)
class OrderBilled(IntegrationEvent):
    order_id: str
    source: ClassVar[str] = "//test-service"
    version: int = 1


@dataclass(frozen=True, kw_only=True, eq=False)
class OrderShipped(IntegrationEvent):
    order_id: str
    source: ClassVar[str] = "//test-service"
    version: int = 1


@dataclass(frozen=True, kw_only=True, eq=False)
class OrderCancelled(IntegrationEvent):
    order_id: str
    source: ClassVar[str] = "//test-service"
    version: int = 1


@dataclass(frozen=True, kw_only=True, eq=False)
class OrderEscalated(IntegrationEvent):
    order_id: str
    source: ClassVar[str] = "//test-service"
    version: int = 1


@dataclass
class EscalationTimeout:
    order_id: str


# ─── Shared saga data ──────────────────────────────────────────────────────────


@dataclass(eq=False)
class ShippingPolicyData(SagaData):
    order_id: str = ""
    is_order_placed: bool = False
    is_order_billed: bool = False


# ─── Shared saga ──────────────────────────────────────────────────────────────


class ShippingPolicy(Saga[ShippingPolicyData]):

    def configure_how_to_find_saga(self, mapper: SagaMapper[ShippingPolicyData]) -> None:
        mapper.map_saga("order_id") \
            .to_message(OrderPlaced, "order_id") \
            .to_message(OrderBilled, "order_id") \
            .to_message(OrderShipped, "order_id")

    @started_by
    def on_order_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
        self.data.order_id = msg.order_id
        self.data.is_order_placed = True

    @started_by
    def on_order_billed(self, msg: OrderBilled, ctx: SagaContext) -> None:
        self.data.order_id = msg.order_id
        self.data.is_order_billed = True

    @handles
    def on_order_shipped(self, msg: OrderShipped, ctx: SagaContext) -> None:
        self.mark_as_complete()


# ─── Repository factory ────────────────────────────────────────────────────────


def make_repo() -> InMemoryPersistableRepository[UUID, ShippingPolicyData]:
    repo: InMemoryPersistableRepository[UUID, ShippingPolicyData] = (
        InMemoryPersistableRepository()
    )
    repo.session = {}
    return repo


def _make_timeout_repo() -> InMemoryPersistableRepository[UUID, SagaTimeout]:
    repo: InMemoryPersistableRepository[UUID, SagaTimeout] = InMemoryPersistableRepository()
    repo.session = {}
    return repo


# ─── @started_by ──────────────────────────────────────────────────────────────


class TestStartedBy:
    def test_creates_new_saga_on_first_message(self) -> None:
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        manager.handle(OrderPlaced(order_id="order-1"))

        all_data = list(repo.session.values())
        assert len(all_data) == 1
        data = all_data[0]
        assert isinstance(data, ShippingPolicyData)
        assert data.order_id == "order-1"  # type: ignore[attr-defined]
        assert data.is_order_placed is True  # type: ignore[attr-defined]
        assert data.completed is False

    def test_reuses_existing_saga_when_correlation_matches(self) -> None:
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        manager.handle(OrderPlaced(order_id="order-1"))
        manager.handle(OrderBilled(order_id="order-1"))

        assert len(repo.session) == 1, "Should be a single saga instance"
        data = list(repo.session.values())[0]
        assert isinstance(data, ShippingPolicyData)
        assert data.is_order_placed is True  # type: ignore[attr-defined]
        assert data.is_order_billed is True  # type: ignore[attr-defined]

    def test_creates_separate_sagas_for_different_correlation_values(self) -> None:
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        manager.handle(OrderPlaced(order_id="order-1"))
        manager.handle(OrderPlaced(order_id="order-2"))

        assert len(repo.session) == 2


# ─── @handles ─────────────────────────────────────────────────────────────────


class TestHandles:
    def test_raises_when_no_saga_exists(self) -> None:
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        with pytest.raises(SagaNotFoundError) as exc_info:
            manager.handle(OrderShipped(order_id="order-1"))

        assert exc_info.value.event_type is OrderShipped

    def test_handles_subsequent_message_on_existing_saga(self) -> None:
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        manager.handle(OrderPlaced(order_id="order-1"))
        manager.handle(OrderShipped(order_id="order-1"))

        assert len(repo.session) == 0, "Completed saga should be deleted"


# ─── mark_as_complete ─────────────────────────────────────────────────────────


class TestMarkAsComplete:
    def test_delete_on_complete(self) -> None:
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        manager.handle(OrderPlaced(order_id="order-1"))
        assert len(repo.session) == 1

        manager.handle(OrderShipped(order_id="order-1"))
        assert len(repo.session) == 0

    def test_immediate_complete_in_started_by_handler(self) -> None:
        @dataclass(eq=False)
        class InstantData(SagaData):
            pass

        class InstantSaga(Saga[InstantData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[InstantData]) -> None:
                mapper.map_saga("id").to_message(OrderPlaced, "order_id")

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                self.mark_as_complete()

        repo2: InMemoryPersistableRepository[UUID, InstantData] = (
            InMemoryPersistableRepository()
        )
        repo2.session = {}
        manager2 = SagaManager(InstantSaga, repo2, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())
        manager2.handle(OrderPlaced(order_id="x"))

        assert len(repo2.session) == 0, "Never-persisted completed saga should not appear"


# ─── Correlation ──────────────────────────────────────────────────────────────


class TestCorrelation:
    def test_no_correlation_always_creates_new(self) -> None:
        @dataclass(eq=False)
        class NoCorrData(SagaData):
            pass

        class NoCorrSaga(Saga[NoCorrData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[NoCorrData]) -> None:
                pass  # no correlation → every message creates a new saga

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                pass

        repo: InMemoryPersistableRepository[UUID, NoCorrData] = (
            InMemoryPersistableRepository()
        )
        repo.session = {}
        manager = SagaManager(NoCorrSaga, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())
        manager.handle(OrderPlaced(order_id="x"))
        manager.handle(OrderPlaced(order_id="x"))

        assert len(repo.session) == 2

    def test_correlation_isolates_by_field_value(self) -> None:
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        manager.handle(OrderPlaced(order_id="A"))
        manager.handle(OrderBilled(order_id="A"))
        manager.handle(OrderPlaced(order_id="B"))

        assert len(repo.session) == 2
        saga_a = next(
            v for v in repo.session.values()
            if isinstance(v, ShippingPolicyData) and v.order_id == "A"  # type: ignore[attr-defined]
        )
        assert saga_a.is_order_billed is True  # type: ignore[attr-defined]
        saga_b = next(
            v for v in repo.session.values()
            if isinstance(v, ShippingPolicyData) and v.order_id == "B"  # type: ignore[attr-defined]
        )
        assert saga_b.is_order_billed is False  # type: ignore[attr-defined]


# ─── SagaContext.publish ───────────────────────────────────────────────────────


class TestSagaContextPublish:
    def test_publish_calls_publisher(self) -> None:
        publisher: IExternalEventPublisher[Any] = create_autospec(_NullPublisher)

        @dataclass(eq=False)
        class PubData(SagaData):
            order_id: str = ""

        class PubSaga(Saga[PubData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[PubData]) -> None:
                mapper.map_saga("order_id").to_message(OrderPlaced, "order_id")

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                ctx.publish(OrderEscalated(order_id=self.data.order_id))

        repo: InMemoryPersistableRepository[UUID, PubData] = (
            InMemoryPersistableRepository()
        )
        repo.session = {}
        manager = SagaManager(PubSaga, repo, publisher=publisher, timeout_repo=_make_timeout_repo())
        manager.handle(OrderPlaced(order_id="x"))

        publisher.publish.assert_called_once()  # type: ignore[attr-defined]
        events = cast(list[IntegrationEvent], publisher.publish.call_args[0][0])  # type: ignore[attr-defined]
        assert len(events) == 1
        assert isinstance(events[0], OrderEscalated)


# ─── Timeout scheduling ────────────────────────────────────────────────────────


class TestTimeouts:
    def test_request_timeout_saves_to_repo(self) -> None:
        timeout_repo = _make_timeout_repo()

        @dataclass(eq=False)
        class TData(SagaData):
            order_id: str = ""

        class TSaga(Saga[TData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[TData]) -> None:
                mapper.map_saga("order_id").to_message(OrderPlaced, "order_id")

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                self.data.order_id = msg.order_id
                ctx.request_timeout(EscalationTimeout(order_id=msg.order_id), timedelta(hours=24))

        repo: InMemoryPersistableRepository[UUID, TData] = (
            InMemoryPersistableRepository()
        )
        repo.session = {}
        manager = SagaManager(TSaga, repo, publisher=_NullPublisher(), timeout_repo=timeout_repo)
        manager.handle(OrderPlaced(order_id="order-1"))

        assert len(timeout_repo.session) == 1
        timeout = list(timeout_repo.session.values())[0]
        assert isinstance(timeout, SagaTimeout)
        assert timeout.saga_type_name == "TSaga"
        assert isinstance(timeout.state, EscalationTimeout)
        assert timeout.state.order_id == "order-1"  # type: ignore[attr-defined]


    def test_handle_timeout_calls_timeout_handler(self) -> None:
        received: list[EscalationTimeout] = []
        timeout_repo = _make_timeout_repo()

        @dataclass(eq=False)
        class TData3(SagaData):
            order_id: str = ""

        class TSaga3(Saga[TData3]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[TData3]) -> None:
                mapper.map_saga("order_id").to_message(OrderPlaced, "order_id")

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                self.data.order_id = msg.order_id

            @timeout_handler
            def on_escalation(self, state: EscalationTimeout, ctx: SagaContext) -> None:
                received.append(state)
                self.mark_as_complete()

        repo: InMemoryPersistableRepository[UUID, TData3] = (
            InMemoryPersistableRepository()
        )
        repo.session = {}
        manager = SagaManager(TSaga3, repo, publisher=_NullPublisher(), timeout_repo=timeout_repo)
        manager.handle(OrderPlaced(order_id="order-1"))

        saga_id = list(repo.session.keys())[0]
        timeout = SagaTimeout(
            saga_id=saga_id,
            saga_type_name="TSaga3",
            state=EscalationTimeout(order_id="order-1"),
            fire_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )

        manager.handle_timeout(timeout)

        assert len(received) == 1
        assert received[0].order_id == "order-1"
        assert len(repo.session) == 0, "Saga should be deleted after completion"

    def test_handle_timeout_ignores_unknown_saga(self) -> None:
        timeout_repo = _make_timeout_repo()
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=timeout_repo)

        timeout = SagaTimeout(
            saga_id=uuid4(),  # non-existent saga
            saga_type_name="ShippingPolicy",
            state=EscalationTimeout(order_id="x"),
            fire_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        manager.handle_timeout(timeout)  # should not raise

    def test_handle_timeout_ignores_unknown_state_type(self) -> None:
        timeout_repo = _make_timeout_repo()
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=timeout_repo)
        manager.handle(OrderPlaced(order_id="o1"))

        saga_id = list(repo.session.keys())[0]
        timeout = SagaTimeout(
            saga_id=saga_id,
            saga_type_name="ShippingPolicy",
            state="unknown_state_type",  # no @timeout_handler for str
            fire_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        manager.handle_timeout(timeout)  # should silently no-op
        assert len(repo.session) == 1, "Saga should still exist"


# ─── IEventHandler protocol ───────────────────────────────────────────────────


class TestEventHandlerProtocol:
    def test_manager_routes_events_via_handle(self) -> None:
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        manager.handle(OrderPlaced(order_id="order-1"))

        assert len(repo.session) == 1

    def test_manager_is_callable(self) -> None:
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        manager(OrderPlaced(order_id="order-2"))  # __call__ protocol

        assert len(repo.session) == 1


# ─── Error cases ──────────────────────────────────────────────────────────────


class TestErrors:
    def test_handle_unregistered_event_raises(self) -> None:
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        with pytest.raises(TypeError, match="has no handler"):
            manager.handle(OrderCancelled(order_id="x"))

    def test_saga_not_found_error_carries_event(self) -> None:
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())
        event = OrderShipped(order_id="missing")

        with pytest.raises(SagaNotFoundError) as exc_info:
            manager.handle(event)

        assert exc_info.value.event is event
        assert exc_info.value.event_type is OrderShipped


# ─── Compensation fixtures ─────────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True, eq=False)
class BookingStarted(IntegrationEvent):
    booking_id: str
    source: ClassVar[str] = "//test-service"
    version: int = 1


@dataclass(frozen=True, kw_only=True, eq=False)
class FlightReserved(IntegrationEvent):
    booking_id: str
    flight_id: str
    source: ClassVar[str] = "//test-service"
    version: int = 1


@dataclass(frozen=True, kw_only=True, eq=False)
class HotelBooked(IntegrationEvent):
    booking_id: str
    hotel_id: str
    source: ClassVar[str] = "//test-service"
    version: int = 1


@dataclass(frozen=True, kw_only=True, eq=False)
class HotelBookingFailed(IntegrationEvent):
    booking_id: str
    source: ClassVar[str] = "//test-service"
    version: int = 1


@dataclass(frozen=True, kw_only=True, eq=False)
class CancelFlight(IntegrationEvent):
    flight_id: str
    source: ClassVar[str] = "//test-service"
    version: int = 1


@dataclass(frozen=True, kw_only=True, eq=False)
class CancelBooking(IntegrationEvent):
    booking_id: str
    source: ClassVar[str] = "//test-service"
    version: int = 1


@dataclass(eq=False)
class BookingData(SagaData):
    booking_id: str = ""
    flight_id: str = ""
    hotel_id: str = ""


class BookingFlowSaga(Saga[BookingData]):
    """Two-step saga (book flight, then hotel) with full compensation on failure."""

    def configure_how_to_find_saga(self, mapper: SagaMapper[BookingData]) -> None:
        mapper.map_saga("booking_id") \
            .to_message(BookingStarted, "booking_id") \
            .to_message(FlightReserved, "booking_id") \
            .to_message(HotelBooked, "booking_id") \
            .to_message(HotelBookingFailed, "booking_id")

    @started_by
    def on_booking_started(self, msg: BookingStarted, ctx: SagaContext) -> None:
        self.data.booking_id = msg.booking_id

    @compensates(on_booking_started)
    def cancel_booking(self, ctx: SagaContext) -> None:
        ctx.publish(CancelBooking(booking_id=self.data.booking_id))

    @handles
    def on_flight_reserved(self, msg: FlightReserved, ctx: SagaContext) -> None:
        self.data.flight_id = msg.flight_id

    @compensates(on_flight_reserved)
    def cancel_flight(self, ctx: SagaContext) -> None:
        ctx.publish(CancelFlight(flight_id=self.data.flight_id))

    @handles
    def on_hotel_booked(self, msg: HotelBooked, ctx: SagaContext) -> None:
        self.data.hotel_id = msg.hotel_id
        self.mark_as_complete()

    @handles
    def on_hotel_booking_failed(self, msg: HotelBookingFailed, ctx: SagaContext) -> None:
        self.compensate(ctx)
        self.mark_as_complete()


def make_booking_repo() -> InMemoryPersistableRepository[UUID, BookingData]:
    repo: InMemoryPersistableRepository[UUID, BookingData] = InMemoryPersistableRepository()
    repo.session = {}
    return repo


# ─── @compensates ─────────────────────────────────────────────────────────────


class TestCompensation:
    def test_compensation_dispatch_built_at_class_definition(self) -> None:
        assert "on_booking_started" in BookingFlowSaga.compensation_dispatch
        assert "on_flight_reserved" in BookingFlowSaga.compensation_dispatch
        assert "on_hotel_booked" not in BookingFlowSaga.compensation_dispatch

    def test_compensates_invalid_forward_raises_at_class_definition(self) -> None:
        def not_a_handler() -> None:
            pass

        with pytest.raises(TypeError, match="not a @started_by or @handles"):
            class _BadSaga(Saga[BookingData]):  # pyright: ignore[reportUnusedClass]
                def configure_how_to_find_saga(self, mapper: SagaMapper[BookingData]) -> None:
                    pass

                @compensates(not_a_handler)  # type: ignore[arg-type]
                def bad_compensator(self, ctx: SagaContext) -> None:
                    pass

    def test_completed_steps_accumulate_in_order(self) -> None:
        repo = make_booking_repo()
        manager = SagaManager(BookingFlowSaga, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        manager.handle(BookingStarted(booking_id="b-1"))
        manager.handle(FlightReserved(booking_id="b-1", flight_id="f-1"))

        data = list(repo.session.values())[0]
        assert isinstance(data, BookingData)
        assert data.completed_steps == ["on_booking_started", "on_flight_reserved"]

    def test_compensate_runs_compensators_in_lifo_order(self) -> None:
        call_order: list[str] = []

        @dataclass(eq=False)
        class TrackData(SagaData):
            booking_id: str = ""

        class TrackSaga(Saga[TrackData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[TrackData]) -> None:
                mapper.map_saga("booking_id") \
                    .to_message(BookingStarted, "booking_id") \
                    .to_message(FlightReserved, "booking_id") \
                    .to_message(HotelBookingFailed, "booking_id")

            @started_by
            def on_booking_started(self, msg: BookingStarted, ctx: SagaContext) -> None:
                self.data.booking_id = msg.booking_id

            @compensates(on_booking_started)
            def cancel_booking(self, ctx: SagaContext) -> None:
                call_order.append("cancel_booking")

            @handles
            def on_flight_reserved(self, msg: FlightReserved, ctx: SagaContext) -> None:
                pass

            @compensates(on_flight_reserved)
            def cancel_flight(self, ctx: SagaContext) -> None:
                call_order.append("cancel_flight")

            @handles
            def on_hotel_booking_failed(self, msg: HotelBookingFailed, ctx: SagaContext) -> None:
                self.compensate(ctx)
                self.mark_as_complete()

        repo: InMemoryPersistableRepository[UUID, TrackData] = InMemoryPersistableRepository()
        repo.session = {}
        manager = SagaManager(TrackSaga, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        manager.handle(BookingStarted(booking_id="b-1"))
        manager.handle(FlightReserved(booking_id="b-1", flight_id="f-1"))
        manager.handle(HotelBookingFailed(booking_id="b-1"))

        assert call_order == ["cancel_flight", "cancel_booking"]

    def test_compensate_publishes_integration_events(self) -> None:
        published: list[list[IntegrationEvent]] = []

        class _Publisher(_NullPublisher):
            def publish(self, events: Iterable[IntegrationEvent]) -> None:
                published.append(list(events))

        repo = make_booking_repo()
        manager = SagaManager(BookingFlowSaga, repo, publisher=_Publisher(), timeout_repo=_make_timeout_repo())

        manager.handle(BookingStarted(booking_id="b-1"))
        manager.handle(FlightReserved(booking_id="b-1", flight_id="f-42"))
        manager.handle(HotelBookingFailed(booking_id="b-1"))

        # compensation ran: cancel_flight then cancel_booking — both published
        all_events = [e for batch in published for e in batch]
        types = [type(e) for e in all_events]
        assert CancelFlight in types
        assert CancelBooking in types
        cancel_flight_ev = next(e for e in all_events if isinstance(e, CancelFlight))
        assert cancel_flight_ev.flight_id == "f-42"

    def test_compensate_accesses_saga_data(self) -> None:
        captured: list[str] = []

        @dataclass(eq=False)
        class DataSaga_Data(SagaData):
            booking_id: str = ""
            flight_id: str = ""

        class DataSaga(Saga[DataSaga_Data]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[DataSaga_Data]) -> None:
                mapper.map_saga("booking_id") \
                    .to_message(BookingStarted, "booking_id") \
                    .to_message(FlightReserved, "booking_id") \
                    .to_message(HotelBookingFailed, "booking_id")

            @started_by
            def on_booking_started(self, msg: BookingStarted, ctx: SagaContext) -> None:
                self.data.booking_id = msg.booking_id

            @handles
            def on_flight_reserved(self, msg: FlightReserved, ctx: SagaContext) -> None:
                self.data.flight_id = msg.flight_id

            @compensates(on_flight_reserved)
            def cancel_flight(self, ctx: SagaContext) -> None:
                captured.append(self.data.flight_id)

            @handles
            def on_hotel_booking_failed(self, msg: HotelBookingFailed, ctx: SagaContext) -> None:
                self.compensate(ctx)
                self.mark_as_complete()

        repo: InMemoryPersistableRepository[UUID, DataSaga_Data] = InMemoryPersistableRepository()
        repo.session = {}
        manager = SagaManager(DataSaga, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        manager.handle(BookingStarted(booking_id="b-1"))
        manager.handle(FlightReserved(booking_id="b-1", flight_id="UA-789"))
        manager.handle(HotelBookingFailed(booking_id="b-1"))

        assert captured == ["UA-789"]

    def test_steps_without_compensator_are_silently_skipped(self) -> None:
        call_order: list[str] = []

        @dataclass(eq=False)
        class SkipData(SagaData):
            booking_id: str = ""

        class SkipSaga(Saga[SkipData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[SkipData]) -> None:
                mapper.map_saga("booking_id") \
                    .to_message(BookingStarted, "booking_id") \
                    .to_message(FlightReserved, "booking_id") \
                    .to_message(HotelBookingFailed, "booking_id")

            @started_by
            def on_booking_started(self, msg: BookingStarted, ctx: SagaContext) -> None:
                self.data.booking_id = msg.booking_id

            # No @compensates for on_booking_started

            @handles
            def on_flight_reserved(self, msg: FlightReserved, ctx: SagaContext) -> None:
                pass

            @compensates(on_flight_reserved)
            def cancel_flight(self, ctx: SagaContext) -> None:
                call_order.append("cancel_flight")

            @handles
            def on_hotel_booking_failed(self, msg: HotelBookingFailed, ctx: SagaContext) -> None:
                self.compensate(ctx)
                self.mark_as_complete()

        repo: InMemoryPersistableRepository[UUID, SkipData] = InMemoryPersistableRepository()
        repo.session = {}
        manager = SagaManager(SkipSaga, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        manager.handle(BookingStarted(booking_id="b-1"))
        manager.handle(FlightReserved(booking_id="b-1", flight_id="f-1"))
        manager.handle(HotelBookingFailed(booking_id="b-1"))

        # on_booking_started has no compensator — only cancel_flight ran
        assert call_order == ["cancel_flight"]

    def test_completing_handler_step_not_added_to_completed_steps(self) -> None:
        repo = make_booking_repo()
        manager = SagaManager(BookingFlowSaga, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        manager.handle(BookingStarted(booking_id="b-1"))
        manager.handle(FlightReserved(booking_id="b-1", flight_id="f-1"))
        manager.handle(HotelBooked(booking_id="b-1", hotel_id="h-1"))

        # Saga completed and was deleted; on_hotel_booked is NOT in completed_steps
        assert len(repo.session) == 0  # saga deleted after completion

    def test_compensation_only_undoes_steps_that_completed(self) -> None:
        # If flight reservation never happened, its compensator must not run.
        call_order: list[str] = []

        @dataclass(eq=False)
        class EarlyFailData(SagaData):
            booking_id: str = ""

        class EarlyFailSaga(Saga[EarlyFailData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[EarlyFailData]) -> None:
                mapper.map_saga("booking_id") \
                    .to_message(BookingStarted, "booking_id") \
                    .to_message(HotelBookingFailed, "booking_id")

            @started_by
            def on_booking_started(self, msg: BookingStarted, ctx: SagaContext) -> None:
                self.data.booking_id = msg.booking_id

            @compensates(on_booking_started)
            def cancel_booking(self, ctx: SagaContext) -> None:
                call_order.append("cancel_booking")

            @handles
            def on_hotel_booking_failed(self, msg: HotelBookingFailed, ctx: SagaContext) -> None:
                self.compensate(ctx)
                self.mark_as_complete()

        repo: InMemoryPersistableRepository[UUID, EarlyFailData] = InMemoryPersistableRepository()
        repo.session = {}
        manager = SagaManager(EarlyFailSaga, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        # Skip FlightReserved entirely — fail immediately after booking started
        manager.handle(BookingStarted(booking_id="b-1"))
        manager.handle(HotelBookingFailed(booking_id="b-1"))

        assert call_order == ["cancel_booking"]


# ─── on_complete callback ─────────────────────────────────────────────────────


class TestOnComplete:
    def test_called_when_saga_completes(self) -> None:
        completed: list[UUID] = []
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo(), on_complete=completed.append)

        manager.handle(OrderPlaced(order_id="order-1"))
        saga_id = list(repo.session.keys())[0]
        manager.handle(OrderShipped(order_id="order-1"))

        assert saga_id in completed

    def test_not_called_when_saga_still_running(self) -> None:
        completed: list[UUID] = []
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo(), on_complete=completed.append)

        manager.handle(OrderPlaced(order_id="order-1"))

        assert len(completed) == 0

    def test_called_from_background_thread(self) -> None:
        done = threading.Event()
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo(), on_complete=lambda _: done.set())

        manager.handle(OrderPlaced(order_id="order-1"))

        def complete_later() -> None:
            time.sleep(0.05)
            manager.handle(OrderShipped(order_id="order-1"))

        t = threading.Thread(target=complete_later, daemon=True)
        t.start()

        assert done.wait(timeout=5.0) is True
        t.join()
        assert len(repo.session) == 0

    def test_called_via_timeout_handler(self) -> None:
        """Saga completing inside handle_timeout also fires on_complete."""
        completed: list[UUID] = []
        timeout_repo: InMemoryPersistableRepository[UUID, SagaTimeout] = InMemoryPersistableRepository()
        timeout_repo.session = {}

        @dataclass(eq=False)
        class TData(SagaData):
            order_id: str = ""

        class TSaga(Saga[TData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[TData]) -> None:
                mapper.map_saga("order_id").to_message(OrderPlaced, "order_id")

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                self.data.order_id = msg.order_id

            @timeout_handler
            def on_timeout(self, state: EscalationTimeout, ctx: SagaContext) -> None:
                self.mark_as_complete()

        repo: InMemoryPersistableRepository[UUID, TData] = InMemoryPersistableRepository()
        repo.session = {}
        manager: SagaManager[TData] = SagaManager(
            TSaga, repo, publisher=_NullPublisher(), timeout_repo=timeout_repo, on_complete=completed.append
        )
        manager.handle(OrderPlaced(order_id="order-1"))

        saga_id = list(repo.session.keys())[0]
        timeout = SagaTimeout(
            saga_id=saga_id,
            saga_type_name="TSaga",
            state=EscalationTimeout(order_id="order-1"),
            fire_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        manager.handle_timeout(timeout)

        assert saga_id in completed
        assert len(repo.session) == 0


# ─── Concurrent instances ─────────────────────────────────────────────────────


class TestConcurrentInstances:
    def test_two_instances_same_type_run_independently(self) -> None:
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        manager.handle(OrderPlaced(order_id="A"))
        manager.handle(OrderPlaced(order_id="B"))
        manager.handle(OrderBilled(order_id="A"))

        assert len(repo.session) == 2
        data_a = next(
            v for v in repo.session.values()
            if isinstance(v, ShippingPolicyData) and v.order_id == "A"
        )
        data_b = next(
            v for v in repo.session.values()
            if isinstance(v, ShippingPolicyData) and v.order_id == "B"
        )
        assert data_a.is_order_billed is True
        assert data_b.is_order_billed is False

    def test_completing_one_instance_does_not_affect_another(self) -> None:
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        manager.handle(OrderPlaced(order_id="A"))
        manager.handle(OrderPlaced(order_id="B"))

        # Complete saga A
        manager.handle(OrderShipped(order_id="A"))

        assert len(repo.session) == 1
        remaining = list(repo.session.values())[0]
        assert isinstance(remaining, ShippingPolicyData)
        assert remaining.order_id == "B"

    def test_on_complete_is_isolated_per_instance(self) -> None:
        completed: list[UUID] = []
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo(), on_complete=completed.append)

        manager.handle(OrderPlaced(order_id="A"))
        manager.handle(OrderPlaced(order_id="B"))

        ids = {v.order_id: k for k, v in repo.session.items() if isinstance(v, ShippingPolicyData)}  # type: ignore[attr-defined]
        saga_id_a = ids["A"]
        saga_id_b = ids["B"]

        manager.handle(OrderShipped(order_id="A"))

        assert saga_id_a in completed
        assert saga_id_b not in completed

    def test_multiple_managers_for_different_saga_types_are_independent(self) -> None:
        @dataclass(eq=False)
        class BillingData(SagaData):
            order_id: str = ""
            billed: bool = False

        class BillingSaga(Saga[BillingData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[BillingData]) -> None:
                mapper.map_saga("order_id").to_message(OrderPlaced, "order_id")

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                self.data.order_id = msg.order_id
                self.data.billed = True

        shipping_repo = make_repo()
        billing_repo: InMemoryPersistableRepository[UUID, BillingData] = InMemoryPersistableRepository()
        billing_repo.session = {}

        shipping_manager = SagaManager(ShippingPolicy, shipping_repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())
        billing_manager = SagaManager(BillingSaga, billing_repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        shipping_manager.handle(OrderPlaced(order_id="order-1"))
        billing_manager.handle(OrderPlaced(order_id="order-1"))

        assert len(shipping_repo.session) == 1
        assert len(billing_repo.session) == 1

        shipping_data = list(shipping_repo.session.values())[0]
        billing_data = list(billing_repo.session.values())[0]
        assert isinstance(shipping_data, ShippingPolicyData)
        assert isinstance(billing_data, BillingData)

    def test_concurrent_threads_each_complete_their_own_saga(self) -> None:
        N = 10
        repo = make_repo()
        manager = SagaManager(ShippingPolicy, repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())

        order_ids = [f"order-{i}" for i in range(N)]
        errors: list[Exception] = []

        def run_saga(order_id: str) -> None:
            try:
                manager.handle(OrderPlaced(order_id=order_id))
                manager.handle(OrderShipped(order_id=order_id))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run_saga, args=(oid,)) for oid in order_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Errors in threads: {errors}"
        assert len(repo.session) == 0, "All sagas should be completed and deleted"


# ─── Cross-component transactions ─────────────────────────────────────────────


class TestCrossComponentTransactions:
    def test_events_published_only_after_saga_persisted(self) -> None:
        """Events buffered in ctx.publish() are dispatched only after repo.save() succeeds."""
        persist_calls: list[str] = []
        publish_calls: list[str] = []

        @dataclass(eq=False)
        class TxData(SagaData):
            order_id: str = ""

        class TxSaga(Saga[TxData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[TxData]) -> None:
                mapper.map_saga("order_id").to_message(OrderPlaced, "order_id")

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                self.data.order_id = msg.order_id
                ctx.publish(OrderEscalated(order_id=msg.order_id))

        base_repo: InMemoryPersistableRepository[UUID, TxData] = InMemoryPersistableRepository()
        base_repo.session = {}

        class InstrumentedRepo(InMemoryPersistableRepository[UUID, TxData]):
            def save(self, obj: TxData) -> UUID:
                persist_calls.append("saved")
                return super().save(obj)

        instrumented = InstrumentedRepo()
        instrumented.session = base_repo.session

        class InstrumentedPublisher(_NullPublisher):
            def publish(self, events: Iterable[IntegrationEvent]) -> None:
                publish_calls.append("published")
                assert persist_calls == ["saved"], (
                    "Events must be published only AFTER saga data is persisted"
                )

        manager = SagaManager(TxSaga, instrumented, publisher=InstrumentedPublisher(), timeout_repo=_make_timeout_repo())
        manager.handle(OrderPlaced(order_id="order-1"))

        assert persist_calls == ["saved"]
        assert publish_calls == ["published"]

    def test_events_not_published_when_handler_raises(self) -> None:
        published: list[Any] = []

        @dataclass(eq=False)
        class FailData(SagaData):
            pass

        class FailSaga(Saga[FailData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[FailData]) -> None:
                pass

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                ctx.publish(OrderEscalated(order_id=msg.order_id))
                raise RuntimeError("handler exploded")

        class CapturingPublisher(_NullPublisher):
            def publish(self, events: Iterable[IntegrationEvent]) -> None:
                published.extend(events)

        repo: InMemoryPersistableRepository[UUID, FailData] = InMemoryPersistableRepository()
        repo.session = {}
        manager = SagaManager(FailSaga, repo, publisher=CapturingPublisher(), timeout_repo=_make_timeout_repo())

        with pytest.raises(RuntimeError, match="handler exploded"):
            manager.handle(OrderPlaced(order_id="order-1"))

        assert published == [], "No events must be published when the handler raises"
        assert len(repo.session) == 0, "Saga must not be persisted when handler raises"

    def test_saga_with_compensation_uses_publisher_for_cross_service_undo(self) -> None:
        """Demonstrates using saga compensation to publish cross-service cancel commands."""
        published_events: list[IntegrationEvent] = []

        class RecordingPublisher(_NullPublisher):
            def publish(self, events: Iterable[IntegrationEvent]) -> None:
                published_events.extend(events)

        repo = make_booking_repo()
        manager = SagaManager(BookingFlowSaga, repo, publisher=RecordingPublisher(), timeout_repo=_make_timeout_repo())

        manager.handle(BookingStarted(booking_id="trip-1"))
        manager.handle(FlightReserved(booking_id="trip-1", flight_id="flight-99"))
        manager.handle(HotelBookingFailed(booking_id="trip-1"))

        type_names = [type(e).__name__ for e in published_events]
        assert "CancelFlight" in type_names
        assert "CancelBooking" in type_names

        flight_cancel = next(e for e in published_events if isinstance(e, CancelFlight))
        assert flight_cancel.flight_id == "flight-99"

        # Saga deleted — the process is done
        assert len(repo.session) == 0


class TestSagaContextLock:
    """Tests for ``SagaContext.lock()`` wired into ``SagaManager``."""

    def _make_lock_repo(self) -> InMemoryPersistableRepository[UUID, ShippingPolicyData]:
        repo: InMemoryPersistableRepository[UUID, ShippingPolicyData] = InMemoryPersistableRepository()
        repo.session = {}
        return repo

    def test_lock_acquired_during_handler_released_on_completion(self) -> None:
        """Lock acquired via ctx.lock() is released when the saga completes."""
        lock_target_id = uuid4()
        aggregate_repo: InMemoryPersistableRepository[UUID, ShippingPolicyData] = self._make_lock_repo()

        class LockingSaga(Saga[ShippingPolicyData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[ShippingPolicyData]) -> None:
                mapper.map_saga("order_id").to_message(OrderPlaced, "order_id")

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                ctx.lock(aggregate_repo, lock_target_id)
                self.mark_as_complete()

        saga_repo = make_repo()
        manager = SagaManager(LockingSaga, saga_repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())
        manager.handle(OrderPlaced(order_id="order-1"))

        # After completion, the lock must be gone — another owner can acquire it immediately
        aggregate_repo.acquire_lock(lock_target_id, owner="new-owner", timeout=0.0)
        aggregate_repo.release_lock(lock_target_id, owner="new-owner")

    def test_lock_released_on_handler_exception(self) -> None:
        """Lock acquired via ctx.lock() is released when the handler raises."""
        lock_target_id = uuid4()
        aggregate_repo: InMemoryPersistableRepository[UUID, ShippingPolicyData] = self._make_lock_repo()

        class BoomSaga(Saga[ShippingPolicyData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[ShippingPolicyData]) -> None:
                pass

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                ctx.lock(aggregate_repo, lock_target_id)
                raise RuntimeError("handler failed after locking")

        saga_repo = make_repo()
        manager = SagaManager(BoomSaga, saga_repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())
        with pytest.raises(RuntimeError, match="handler failed after locking"):
            manager.handle(OrderPlaced(order_id="order-1"))

        # Lock must be released — another owner acquires without blocking
        aggregate_repo.acquire_lock(lock_target_id, owner="new-owner", timeout=0.0)
        aggregate_repo.release_lock(lock_target_id, owner="new-owner")

    def test_lock_persists_across_saga_steps(self) -> None:
        """Lock acquired in step 1 is still held in step 2."""
        lock_target_id = uuid4()
        aggregate_repo: InMemoryPersistableRepository[UUID, ShippingPolicyData] = self._make_lock_repo()
        lock_held_during_step2: list[bool] = []

        class MultiStepSaga(Saga[ShippingPolicyData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[ShippingPolicyData]) -> None:
                mapper.map_saga("order_id").to_message(OrderPlaced, "order_id").to_message(OrderBilled, "order_id")

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                self.data.order_id = msg.order_id
                ctx.lock(aggregate_repo, lock_target_id)

            @handles
            def on_billed(self, msg: OrderBilled, ctx: SagaContext) -> None:
                # Lock acquired in step 1 should still be held
                try:
                    aggregate_repo.acquire_lock(lock_target_id, owner="intruder", timeout=0.01)
                    lock_held_during_step2.append(False)
                    aggregate_repo.release_lock(lock_target_id, owner="intruder")
                except LockConflictError:
                    lock_held_during_step2.append(True)
                self.mark_as_complete()

        saga_repo = make_repo()
        manager = SagaManager(MultiStepSaga, saga_repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())
        manager.handle(OrderPlaced(order_id="order-x"))
        manager.handle(OrderBilled(order_id="order-x"))

        assert lock_held_during_step2 == [True], "Lock must still be held during step 2"

        # After completion, lock is released
        aggregate_repo.acquire_lock(lock_target_id, owner="after", timeout=0.0)
        aggregate_repo.release_lock(lock_target_id, owner="after")

    def test_ctx_saga_id_property(self) -> None:
        """SagaContext.saga_id returns the saga instance's UUID."""
        captured_ids: list[UUID] = []

        class IdSaga(Saga[ShippingPolicyData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[ShippingPolicyData]) -> None:
                pass

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                captured_ids.append(ctx.saga_id)

        saga_repo = make_repo()
        manager = SagaManager(IdSaga, saga_repo, publisher=_NullPublisher(), timeout_repo=_make_timeout_repo())
        manager.handle(OrderPlaced(order_id="order-id"))

        assert len(captured_ids) == 1
        assert isinstance(captured_ids[0], UUID)


# ─── TimeoutManager ───────────────────────────────────────────────────────────

# Module-level types needed so get_type_hints() can resolve them inside saga handlers.
@dataclass
class _TimeoutA:
    pass

@dataclass
class _TimeoutB:
    pass


def _run_tm_in_background(tm: TimeoutManager) -> threading.Thread:
    """Start TimeoutManager.start() in a daemon thread and return it."""
    t = threading.Thread(target=tm.start, daemon=True)
    t.start()
    return t


class TestTimeoutManagerInterface:
    """Tests for TimeoutManager's IBackgroundTasks interface."""

    def test_tasks_returns_start(self) -> None:
        tm = TimeoutManager(_make_timeout_repo())
        tasks = tm.tasks()
        assert len(tasks) == 1
        assert tasks[0] == tm.start

    def test_cleanup_does_not_raise(self) -> None:
        tm = TimeoutManager(_make_timeout_repo())
        tm.cleanup()  # must be a no-op

    def test_registered_manager_receives_its_timeouts(self) -> None:
        """A registered manager receives timeouts for its saga type."""
        received: list[Any] = []
        timeout_repo = _make_timeout_repo()

        @dataclass(eq=False)
        class TData(SagaData):
            order_id: str = ""

        class TSaga(Saga[TData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[TData]) -> None:
                mapper.map_saga("order_id").to_message(OrderPlaced, "order_id")

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                self.data.order_id = msg.order_id

            @timeout_handler
            def on_escalation(self, state: EscalationTimeout, ctx: SagaContext) -> None:
                received.append(state)
                self.mark_as_complete()

        saga_repo: InMemoryPersistableRepository[UUID, TData] = InMemoryPersistableRepository()
        saga_repo.session = {}
        manager: SagaManager[TData] = SagaManager(TSaga, saga_repo, publisher=_NullPublisher(), timeout_repo=timeout_repo)
        manager.handle(OrderPlaced(order_id="o1"))

        saga_id = list(saga_repo.session.keys())[0]
        import datetime as _dt
        timeout_repo.save(SagaTimeout(
            saga_id=saga_id, saga_type_name="TSaga",
            state=EscalationTimeout(order_id="o1"),
            fire_at=_dt.datetime.now(_dt.timezone.utc),
        ))

        tm = TimeoutManager(timeout_repo, poll_interval=5.0)
        tm.register(manager)
        _run_tm_in_background(tm)

        time.sleep(0.3)
        assert len(received) == 1

    def test_unregistered_manager_does_not_receive_timeout(self) -> None:
        """A manager not registered with the TimeoutManager never receives timeouts."""
        received: list[Any] = []
        timeout_repo = _make_timeout_repo()

        @dataclass(eq=False)
        class TData(SagaData):
            order_id: str = ""

        class TSaga(Saga[TData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[TData]) -> None:
                mapper.map_saga("order_id").to_message(OrderPlaced, "order_id")

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                self.data.order_id = msg.order_id

            @timeout_handler
            def on_escalation(self, state: EscalationTimeout, ctx: SagaContext) -> None:
                received.append(state)

        saga_repo: InMemoryPersistableRepository[UUID, TData] = InMemoryPersistableRepository()
        saga_repo.session = {}
        manager: SagaManager[TData] = SagaManager(TSaga, saga_repo, publisher=_NullPublisher(), timeout_repo=timeout_repo)
        manager.handle(OrderPlaced(order_id="o1"))

        saga_id = list(saga_repo.session.keys())[0]
        import datetime as _dt
        timeout_repo.save(SagaTimeout(
            saga_id=saga_id, saga_type_name="TSaga",
            state=EscalationTimeout(order_id="o1"),
            fire_at=_dt.datetime.now(_dt.timezone.utc),
        ))

        # TimeoutManager with no managers registered
        tm = TimeoutManager(timeout_repo, poll_interval=5.0)
        _run_tm_in_background(tm)

        time.sleep(0.3)
        assert len(received) == 0


class TestTimeoutManagerDelivery:
    """Tests for timeout delivery behaviour."""

    def test_delivers_expired_timeout(self) -> None:
        """An expired SagaTimeout record is delivered to the correct handler."""
        received: list[EscalationTimeout] = []
        timeout_repo = _make_timeout_repo()

        @dataclass(eq=False)
        class TData(SagaData):
            order_id: str = ""

        class TSaga(Saga[TData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[TData]) -> None:
                mapper.map_saga("order_id").to_message(OrderPlaced, "order_id")

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                self.data.order_id = msg.order_id

            @timeout_handler
            def on_escalation(self, state: EscalationTimeout, ctx: SagaContext) -> None:
                received.append(state)
                self.mark_as_complete()

        saga_repo: InMemoryPersistableRepository[UUID, TData] = InMemoryPersistableRepository()
        saga_repo.session = {}
        manager: SagaManager[TData] = SagaManager(TSaga, saga_repo, publisher=_NullPublisher(), timeout_repo=timeout_repo)
        manager.handle(OrderPlaced(order_id="order-1"))

        saga_id = list(saga_repo.session.keys())[0]
        timeout_repo.save(SagaTimeout(
            saga_id=saga_id,
            saga_type_name="TSaga",
            state=EscalationTimeout(order_id="order-1"),
            fire_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        ))

        tm = TimeoutManager(timeout_repo, poll_interval=5.0)
        tm.register(manager)
        _run_tm_in_background(tm)

        time.sleep(0.3)
        assert len(received) == 1
        assert received[0].order_id == "order-1"

    def test_deletes_timeout_record_after_delivery(self) -> None:
        """The SagaTimeout record is removed from the repo after delivery."""
        timeout_repo = _make_timeout_repo()

        @dataclass(eq=False)
        class TData(SagaData):
            order_id: str = ""

        class TSaga(Saga[TData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[TData]) -> None:
                mapper.map_saga("order_id").to_message(OrderPlaced, "order_id")

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                self.data.order_id = msg.order_id

            @timeout_handler
            def on_escalation(self, state: EscalationTimeout, ctx: SagaContext) -> None:
                self.mark_as_complete()

        saga_repo: InMemoryPersistableRepository[UUID, TData] = InMemoryPersistableRepository()
        saga_repo.session = {}
        manager: SagaManager[TData] = SagaManager(TSaga, saga_repo, publisher=_NullPublisher(), timeout_repo=timeout_repo)
        manager.handle(OrderPlaced(order_id="order-1"))

        saga_id = list(saga_repo.session.keys())[0]
        timeout_repo.save(SagaTimeout(
            saga_id=saga_id,
            saga_type_name="TSaga",
            state=EscalationTimeout(order_id="order-1"),
            fire_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        ))

        tm = TimeoutManager(timeout_repo, poll_interval=5.0)
        tm.register(manager)
        _run_tm_in_background(tm)

        time.sleep(0.3)
        assert len(timeout_repo.session) == 0

    def test_does_not_deliver_future_timeouts(self) -> None:
        """A SagaTimeout whose fire_at is in the future is not delivered early."""
        received: list[Any] = []
        timeout_repo = _make_timeout_repo()

        @dataclass(eq=False)
        class TData(SagaData):
            order_id: str = ""

        class TSaga(Saga[TData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[TData]) -> None:
                mapper.map_saga("order_id").to_message(OrderPlaced, "order_id")

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                self.data.order_id = msg.order_id

            @timeout_handler
            def on_escalation(self, state: EscalationTimeout, ctx: SagaContext) -> None:
                received.append(state)

        saga_repo: InMemoryPersistableRepository[UUID, TData] = InMemoryPersistableRepository()
        saga_repo.session = {}
        manager: SagaManager[TData] = SagaManager(TSaga, saga_repo, publisher=_NullPublisher(), timeout_repo=timeout_repo)
        manager.handle(OrderPlaced(order_id="order-1"))

        saga_id = list(saga_repo.session.keys())[0]
        import datetime as _dt
        far_future = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)
        timeout_repo.save(SagaTimeout(
            saga_id=saga_id,
            saga_type_name="TSaga",
            state=EscalationTimeout(order_id="order-1"),
            fire_at=far_future,
        ))

        tm = TimeoutManager(timeout_repo, poll_interval=0.05)
        tm.register(manager)
        _run_tm_in_background(tm)

        time.sleep(0.2)
        assert len(received) == 0

    def test_deletes_timeout_for_unknown_saga_type(self) -> None:
        """A timeout whose saga_type_name has no registered manager is still deleted."""
        timeout_repo = _make_timeout_repo()
        tm = TimeoutManager(timeout_repo, poll_interval=5.0)
        # no managers registered

        import datetime as _dt
        timeout_repo.save(SagaTimeout(
            saga_id=uuid4(),
            saga_type_name="UnregisteredSaga",
            state=EscalationTimeout(order_id="x"),
            fire_at=_dt.datetime.now(_dt.timezone.utc),
        ))

        _run_tm_in_background(tm)

        time.sleep(0.3)
        assert len(timeout_repo.session) == 0

    def test_routes_to_correct_manager_among_multiple(self) -> None:
        """With two managers registered, each timeout reaches only its own manager."""
        timeout_repo = _make_timeout_repo()
        received_a: list[Any] = []
        received_b: list[Any] = []

        @dataclass(eq=False)
        class DataA(SagaData):
            order_id: str = ""

        @dataclass(eq=False)
        class DataB(SagaData):
            order_id: str = ""

        class SagaA(Saga[DataA]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[DataA]) -> None:
                mapper.map_saga("order_id").to_message(OrderPlaced, "order_id")

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                self.data.order_id = msg.order_id

            @timeout_handler
            def on_timeout(self, state: _TimeoutA, ctx: SagaContext) -> None:
                received_a.append(state)
                self.mark_as_complete()

        class SagaB(Saga[DataB]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[DataB]) -> None:
                mapper.map_saga("order_id").to_message(OrderBilled, "order_id")

            @started_by
            def on_billed(self, msg: OrderBilled, ctx: SagaContext) -> None:
                self.data.order_id = msg.order_id

            @timeout_handler
            def on_timeout(self, state: _TimeoutB, ctx: SagaContext) -> None:
                received_b.append(state)
                self.mark_as_complete()

        repo_a: InMemoryPersistableRepository[UUID, DataA] = InMemoryPersistableRepository()
        repo_a.session = {}
        repo_b: InMemoryPersistableRepository[UUID, DataB] = InMemoryPersistableRepository()
        repo_b.session = {}

        manager_a: SagaManager[DataA] = SagaManager(SagaA, repo_a, publisher=_NullPublisher(), timeout_repo=timeout_repo)
        manager_b: SagaManager[DataB] = SagaManager(SagaB, repo_b, publisher=_NullPublisher(), timeout_repo=timeout_repo)

        manager_a.handle(OrderPlaced(order_id="a-1"))
        manager_b.handle(OrderBilled(order_id="b-1"))

        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc)
        saga_id_a = list(repo_a.session.keys())[0]
        saga_id_b = list(repo_b.session.keys())[0]
        timeout_repo.save(SagaTimeout(saga_id=saga_id_a, saga_type_name="SagaA", state=_TimeoutA(), fire_at=now))
        timeout_repo.save(SagaTimeout(saga_id=saga_id_b, saga_type_name="SagaB", state=_TimeoutB(), fire_at=now))

        tm = TimeoutManager(timeout_repo, poll_interval=5.0)
        tm.register(manager_a)
        tm.register(manager_b)
        _run_tm_in_background(tm)

        time.sleep(0.3)
        assert len(received_a) == 1
        assert isinstance(received_a[0], _TimeoutA)
        assert len(received_b) == 1
        assert isinstance(received_b[0], _TimeoutB)


class TestTimeoutManagerSmartSleep:
    """Tests for smart sleep — TimeoutManager wakes early for the soonest timeout."""

    def test_wakes_before_poll_interval_when_timeout_is_imminent(self) -> None:
        """With a long poll_interval, an imminent timeout is still delivered promptly."""
        received: list[Any] = []
        timeout_repo = _make_timeout_repo()

        @dataclass(eq=False)
        class TData(SagaData):
            order_id: str = ""

        class TSaga(Saga[TData]):
            def configure_how_to_find_saga(self, mapper: SagaMapper[TData]) -> None:
                mapper.map_saga("order_id").to_message(OrderPlaced, "order_id")

            @started_by
            def on_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
                self.data.order_id = msg.order_id

            @timeout_handler
            def on_escalation(self, state: EscalationTimeout, ctx: SagaContext) -> None:
                received.append(state)
                self.mark_as_complete()

        saga_repo: InMemoryPersistableRepository[UUID, TData] = InMemoryPersistableRepository()
        saga_repo.session = {}
        manager: SagaManager[TData] = SagaManager(TSaga, saga_repo, publisher=_NullPublisher(), timeout_repo=timeout_repo)
        manager.handle(OrderPlaced(order_id="order-1"))

        saga_id = list(saga_repo.session.keys())[0]
        import datetime as _dt
        fire_in = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=0.15)
        timeout_repo.save(SagaTimeout(
            saga_id=saga_id,
            saga_type_name="TSaga",
            state=EscalationTimeout(order_id="order-1"),
            fire_at=fire_in,
        ))

        # poll_interval is 10 s — without smart sleep it would never fire in this test
        tm = TimeoutManager(timeout_repo, poll_interval=10.0)
        tm.register(manager)
        start = time.monotonic()
        _run_tm_in_background(tm)

        time.sleep(0.5)  # wait well past fire_at but far less than poll_interval
        elapsed = time.monotonic() - start
        assert len(received) == 1, "Timeout must be delivered before poll_interval expires"
        assert elapsed < 5.0, f"Delivery took {elapsed:.2f}s — smart sleep not working"

    def test_falls_back_to_poll_interval_when_no_timeouts_pending(self) -> None:
        """When no timeouts exist, the loop sleeps for poll_interval (no crash)."""
        timeout_repo = _make_timeout_repo()
        tm = TimeoutManager(timeout_repo, poll_interval=0.05)
        _run_tm_in_background(tm)
        time.sleep(0.2)  # just verify it doesn't crash with an empty repo
