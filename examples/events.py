"""Domain events with aggregates and UnitOfWork.

Scenario: placing an order raises an OrderPlaced event.
On UoW commit the event is forwarded to an email sender.

Run with:
    uv run python examples/events.py
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from hike import DomainEvent, UnitOfWork, UuidAggregate, ValueObject, non_empty, EntityID, command, Field
from hike.events.event_bus import EventBus
from hike.events.event_handlers import DomainEventHandler
from hike.persistence.providers.in_memory import InMemoryDBContext, InMemoryRepository


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OrderPlaced(DomainEvent):
    customer_id: UUID
    order_id: UUID


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

class Email(ValueObject[str]):
    __validators__ = [non_empty]


class Customer(UuidAggregate):
    orders_ids: list[EntityID[UUID]]

    @command
    def add_order(self, order_id: UUID):
        self.orders_ids.append(EntityID(order_id))


class Order(UuidAggregate):
    customer_id: Field[EntityID[UUID]]

    def __post_init__(self) -> None:
        order_placed = OrderPlaced(customer_id=self.customer_id.value, order_id=self.id.value)
        self.raise_event(order_placed)


# ---------------------------------------------------------------------------
# Event handler — sends a confirmation email
# ---------------------------------------------------------------------------

class OrderPlacedAddOrderToCustomer(DomainEventHandler[UUID, Customer, OrderPlaced]):
    def handle(self, event: OrderPlaced) -> None:
        customer = self.repo.get_one(event.customer_id)
        customer.add_order(event.order_id)
        self.repo.update(customer)


# ---------------------------------------------------------------------------
# Wire up and run
# ---------------------------------------------------------------------------

# repositories - one for each aggregate
order_repo = InMemoryRepository()
customer_repo = InMemoryRepository()

# events - wire OrderPlaced event to its handler
event_bus = EventBus()
event_bus.subscribe(OrderPlacedAddOrderToCustomer(customer_repo))

# unit of work
context = InMemoryDBContext()
uow = UnitOfWork(context, event_producer=event_bus)

# create a new order
with uow(order_repo, customer_repo, auto_commit=True):
    new_order = Order(customer_id=EntityID(uuid4()))
    order_repo.save(new_order)
    # ① bus.publish(OrderPlaced) → OrderPlacedAddOrderToCustomer runs
    # ② update customer orders field in database
    # ③ no errors - everything commit in one transaction. errors - everything fails altogether
