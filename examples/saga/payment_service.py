"""Payment service — reacts to ChargeCard events and publishes the outcome.

Simulates an external payment processor that runs on a separate machine and
communicates exclusively through the shared event bus.  The card token
determines the outcome:

* Tokens starting with ``4242`` succeed  → ``PaymentReceived`` published.
* All other tokens are declined          → ``PaymentFailed`` published.

Bus subscription is the caller's responsibility::

    bus = EventBus()
    svc = PaymentService(bus)
    bus.subscribe(svc)
    bus.publish([ChargeCard(...)])   # → PaymentReceived or PaymentFailed lands on bus
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from hike import UnitOfWork, UuidAggregate, command
from hike.events.interfaces import IEventHandler
from hike.events.interfaces.publisher import IEventPublisher
from hike.persistence.providers.in_memory import InMemoryDBContext, InMemoryRepository

from shared import ChargeCard, PaymentFailed, PaymentReceived


class Payment(UuidAggregate):
    """Domain aggregate for a single charge attempt."""

    @command
    def process(self, *, flight_id: str, card_token: str, amount: float) -> None:
        if card_token.startswith("4242"):
            self.raise_event(PaymentReceived(flight_id=flight_id, amount=amount))
        else:
            self.raise_event(PaymentFailed(
                flight_id=flight_id,
                reason=f"card ending {card_token[-4:]} was declined",
            ))


class PaymentService(IEventHandler[ChargeCard]):
    """Processes ``ChargeCard`` events and publishes the payment outcome.

    Every charge attempt produces exactly one ``PaymentReceived`` or
    ``PaymentFailed`` event, raised inside ``Payment.process()`` and
    dispatched to the bus by the UoW on commit.
    """

    def __init__(self, publisher: IEventPublisher[Any]) -> None:
        ctx = InMemoryDBContext()
        self._repo: InMemoryRepository[UUID, Payment] = InMemoryRepository()
        self._uow = UnitOfWork(ctx, event_publisher=publisher)

    def handle(self, event: ChargeCard) -> None:
        with self._uow(self._repo, auto_commit=True):
            payment = Payment()
            payment.process(
                flight_id=event.flight_id,
                card_token=event.card_token,
                amount=event.amount,
            )
            self._repo.save(payment)
