"""Shared types used by both the booking service and the payment service.

Defines the integration events that cross the service boundary and the saga state.
"""

from __future__ import annotations

from dataclasses import dataclass

from hike.events.integration_event import IntegrationEvent
from hike.events.saga import SagaData


# ---------------------------------------------------------------------------
# Integration events — cross the service boundary in both directions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, eq=False)
class FlightReserved(IntegrationEvent):
    """Published by the booking service when a seat is held.

    ``flight_id`` is the seat's own UUID (as a string) and serves as the
    primary correlation key throughout the booking saga lifecycle.
    """

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


