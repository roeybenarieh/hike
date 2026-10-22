"""Shared types used by both the booking service and the payment service.

Defines the integration events that cross the service boundary and the saga state.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from hike import UuidAggregate, command
from hike.events.integration_event import IntegrationEvent
from hike.events.saga import SagaData


# ---------------------------------------------------------------------------
# Integration events — cross the service boundary in both directions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, eq=False)
class FlightReserved(IntegrationEvent):
    """Published by the booking service when a seat is held.

    ``seat_id`` identifies the ``FlightSeat`` aggregate the reservation was
    made against, so the saga can release its lock on completion/compensation.
    ``flight_id`` is the airline's own flight code and is the primary
    correlation key throughout the booking saga lifecycle.
    """

    seat_id: UUID
    flight_id: str
    passenger: str
    card_token: str
    amount: float


@dataclass(frozen=True, kw_only=True, eq=False)
class ChargeCard(IntegrationEvent):
    """Published by the booking saga to request a charge from the payment service.

    The payment service subscribes to this event and publishes either
    ``PaymentReceived`` or ``PaymentFailed`` in response.
    """

    flight_id: str
    card_token: str
    amount: float


@dataclass(frozen=True, kw_only=True, eq=False)
class PaymentReceived(IntegrationEvent):
    """Published by the payment service when a charge succeeds."""

    flight_id: str
    amount: float


@dataclass(frozen=True, kw_only=True, eq=False)
class PaymentFailed(IntegrationEvent):
    """Published by the payment service when a charge is declined."""

    flight_id: str
    reason: str


@dataclass(frozen=True, kw_only=True, eq=False)
class ReservationAcknowledged(IntegrationEvent):
    """Published by the booking service to confirm the reservation was received."""

    flight_id: str
    passenger: str


@dataclass(frozen=True, kw_only=True, eq=False)
class BookingConfirmed(IntegrationEvent):
    """Published by the booking service when the booking is fully confirmed."""

    flight_id: str
    passenger: str


@dataclass(frozen=True, kw_only=True, eq=False)
class ReservationCancelled(IntegrationEvent):
    """Published by the booking service when the reservation is rolled back."""

    flight_id: str
    passenger: str
    reason: str


# ---------------------------------------------------------------------------
# Seat aggregate — owns the reservation decision, raises FlightReserved
# ---------------------------------------------------------------------------


class FlightSeat(UuidAggregate):
    """Domain aggregate for a single bookable seat.

    ``add_seat()`` persists one with no reservation yet; ``reserve()`` then
    raises ``FlightReserved`` (dispatched by the UoW on commit), which starts
    the booking saga.
    """

    @command
    def reserve(self, *, flight_id: str, passenger: str, card_token: str, amount: float) -> None:
        self.raise_event(FlightReserved(
            seat_id=self.id.value,
            flight_id=flight_id,
            passenger=passenger,
            card_token=card_token,
            amount=amount,
        ))


# ---------------------------------------------------------------------------
# Timeout state — not an event; carries context into the @timeout_handler
# ---------------------------------------------------------------------------


@dataclass
class PaymentDeadline:
    """Scheduled by the booking saga; fires when the payment window expires."""

    flight_id: str


# ---------------------------------------------------------------------------
# Saga data — persisted state shared between handler calls
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class FlightBookingData(SagaData):
    flight_id: str = ""
    passenger: str = ""


