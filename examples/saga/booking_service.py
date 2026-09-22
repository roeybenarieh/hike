"""Booking service — owns seat inventory and the FlightBookingSaga process manager.

BookingService is the single entry point for the booking workflow:
  1. Call ``add_seat()`` to make a seat available for booking.
  2. Call ``reserve()`` to start the booking process for a seat.
     This raises FlightReserved, which the saga picks up automatically.

Component responsibilities
--------------------------
* ``FlightBookingSaga`` — saga definition; coordinates payment and cancellation.
* ``BookingService``    — owns seat repo + UoW; starts the workflow via reserve();
  assembles SagaManager and TimeoutManager; exposes ``manager`` for bus wiring.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any, Callable, ClassVar
from uuid import UUID

from hike import UnitOfWork
from hike.events.interfaces.publisher import IEventPublisher
from hike.events.saga import (
    Saga,
    SagaContext,
    SagaManager,
    SagaMapper,
    SagaTimeout,
    TimeoutManager,
    compensates,
    handles,
    started_by,
    timeout_handler,
)
from hike.persistence.providers.in_memory import InMemoryDBContext, InMemoryPersistableRepository, InMemoryRepository

from shared import (
    BookingConfirmed,
    ChargeCard,
    FlightBookingData,
    FlightReserved,
    FlightSeat,
    PaymentDeadline,
    PaymentFailed,
    PaymentReceived,
    ReservationAcknowledged,
    ReservationCancelled,
)


# ---------------------------------------------------------------------------
# Saga definition
# ---------------------------------------------------------------------------


class FlightBookingSaga(Saga[FlightBookingData]):
    """Coordinates the flight-booking lifecycle across payment and reservation.

    Runs inside the booking service.  Correlates all incoming events by
    ``flight_id`` so every step of the same booking lands on the same
    saga instance.
    """

    seat_repo: ClassVar[InMemoryRepository[UUID, FlightSeat]]

    # ── Correlation ───────────────────────────────────────────────────────────

    def configure_how_to_find_saga(self, mapper: SagaMapper[FlightBookingData]) -> None:
        mapper.map_saga("flight_id") \
            .to_message(FlightReserved,  "flight_id") \
            .to_message(PaymentReceived, "flight_id") \
            .to_message(PaymentFailed,   "flight_id")

    # ── Step 1: reservation received ──────────────────────────────────────────

    @started_by
    def on_flight_reserved(self, msg: FlightReserved, ctx: SagaContext) -> None:
        self.data.flight_id = msg.flight_id
        self.data.passenger  = msg.passenger

        # Hold the seat exclusively for the saga's lifetime; released on
        # completion or handler failure.
        ctx.lock(type(self).seat_repo, msg.seat_id)

        ctx.publish(ReservationAcknowledged(
            flight_id=msg.flight_id,
            passenger=msg.passenger,
        ))

        # Request payment from the payment service via the bus — no direct call.
        ctx.publish(ChargeCard(
            flight_id=msg.flight_id,
            card_token=msg.card_token,
            amount=msg.amount,
        ))

        # Open a 24-hour payment window (5 s in this demo — long enough that
        # payment always completes before the deadline in the happy/failure paths).
        ctx.request_timeout(PaymentDeadline(msg.flight_id), timedelta(seconds=5))

    # ── Step 2a: payment succeeded ────────────────────────────────────────────

    @handles
    def on_payment_received(self, msg: PaymentReceived, ctx: SagaContext) -> None:
        ctx.publish(BookingConfirmed(
            flight_id=msg.flight_id,
            passenger=self.data.passenger,
        ))
        self.mark_as_complete()

    # ── Step 2b: payment declined ─────────────────────────────────────────────

    @handles
    def on_payment_failed(self, msg: PaymentFailed, ctx: SagaContext) -> None:
        self.compensate(ctx)
        self.mark_as_complete()

    @compensates(on_flight_reserved)
    def cancel_reservation(self, ctx: SagaContext) -> None:
        ctx.publish(ReservationCancelled(
            flight_id=self.data.flight_id,
            passenger=self.data.passenger,
            reason="payment issue",
        ))

    # ── Step 2c: payment window expired ──────────────────────────────────────

    @timeout_handler
    def on_payment_deadline(self, state: PaymentDeadline, ctx: SagaContext) -> None:
        self.compensate(ctx)
        self.mark_as_complete()


# ---------------------------------------------------------------------------
# Service wrapper
# ---------------------------------------------------------------------------


class BookingService:
    """Entry point for the booking workflow: seat management + saga coordination.

    Usage::

        service = BookingService(bus)
        seat_id = service.add_seat()
        service.reserve(seat_id, flight_id="AA101", passenger="Alice",
                        card_token="4242-...", amount=399.99)

    Pass *on_complete* to be notified when a saga instance finishes — useful
    for waiting on the timeout scenario::

        done = threading.Event()
        service = BookingService(bus, on_complete=lambda _: done.set())
    """

    def __init__(
        self,
        publisher: IEventPublisher[Any],
        *,
        on_complete: Callable[[UUID], None] | None = None,
    ) -> None:
        seat_ctx = InMemoryDBContext()
        self._seat_repo: InMemoryRepository[UUID, FlightSeat] = InMemoryRepository()
        self._seat_uow: UnitOfWork[Any] = UnitOfWork(seat_ctx, event_publisher=publisher)
        FlightBookingSaga.seat_repo = self._seat_repo

        saga_ctx = InMemoryDBContext()
        saga_repo: InMemoryPersistableRepository[UUID, FlightBookingData] = (
            InMemoryPersistableRepository[UUID, FlightBookingData]()
        )

        timeout_ctx = InMemoryDBContext()
        timeout_repo: InMemoryPersistableRepository[UUID, SagaTimeout] = (
            InMemoryPersistableRepository[UUID, SagaTimeout]()
        )
        timeout_uow: UnitOfWork[Any] = UnitOfWork(timeout_ctx)

        self._saga_ctx = saga_ctx
        self.manager = SagaManager(
            FlightBookingSaga, saga_repo,
            publisher=publisher,
            timeout_repo=timeout_repo,
            uow=UnitOfWork(saga_ctx),
            timeout_uow=timeout_uow,
            on_complete=on_complete,
        )

        tm = TimeoutManager(timeout_repo, poll_interval=0.05, uow=timeout_uow)
        tm.register(self.manager)
        threading.Thread(target=tm.start, daemon=True).start()

    def add_seat(self) -> UUID:
        """Persist a new FlightSeat and return its ID."""
        seat = FlightSeat()
        with self._seat_uow(self._seat_repo, auto_commit=True):
            self._seat_repo.save(seat)
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
        """Reserve *seat_id*, triggering FlightReserved and starting the booking saga."""
        with self._seat_uow(self._seat_repo, auto_commit=True):
            seat = self._seat_repo.get_one(seat_id)
            seat.reserve(
                flight_id=flight_id,
                passenger=passenger,
                card_token=card_token,
                amount=amount,
            )
            self._seat_repo.update(seat)

    @property
    def active_sagas(self) -> int:
        return len(self._saga_ctx.committed)
