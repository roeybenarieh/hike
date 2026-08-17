# Domain Events

!!! warning "🚧 Work in Progress"
    This page is actively being written. The API and examples are functional, but some sections may be incomplete or revised before the stable release.

When something important happens in your domain — an order is placed, a payment fails, a user upgrades their subscription — that fact deserves a name and a type. **Domain events** give meaningful things a permanent, typed record of *what happened*, and let other parts of the system react to them without being tightly coupled to the code that triggered them.

---

## Why domain events?

Consider an e-commerce checkout:

1. The `Order` aggregate is saved. ✓
2. An email confirmation should be sent.
3. The product inventory should be decremented.
4. The loyalty-points balance should be updated.

If you put all of this inside `place_order()`, you've coupled `Order` to email, inventory, and loyalty — four separate concerns in one method. Change the email provider? Touch `Order`. Add a new reaction? Touch `Order` again.

Domain events break this coupling. `Order.place()` raises an `OrderPlaced` event and stops there. Email, inventory, and loyalty each subscribe to `OrderPlaced` independently. Neither knows the other exists. Adding a fifth reaction requires zero changes to `Order`.

---

## Defining an event

An event describes something that *has already happened*, so its name is always in the past tense. Events are immutable value objects — use a frozen dataclass:

```python
from dataclasses import dataclass
from hike import DomainEvent

@dataclass(frozen=True)
class OrderPlaced(DomainEvent):
    order_id: str
    customer_id: str
    total: float

@dataclass(frozen=True)
class PaymentFailed(DomainEvent):
    order_id: str
    reason: str
```

> **Keep events small.** An event should contain only what a subscriber needs to react — IDs and key values. If a subscriber needs the full aggregate, it re-fetches it from the repository using the ID from the event.

---

## Raising events from an aggregate

An aggregate raises events from inside its command methods by calling `self.raise_event()`:

```python
from hike import UuidAggregate
from hike.entity import Field, command
from hike.value_object import ValueObject, non_empty, non_negative

class Price(ValueObject[float]):
    __validators__ = [non_negative]

class OrderStatus(ValueObject[str]):
    __validators__ = [non_empty]

class Order(UuidAggregate):
    status: Field[OrderStatus]
    total: Field[Price]

    def place(self, customer_id: str) -> None:
        self.status = OrderStatus("placed")
        self.raise_event(
            OrderPlaced(
                order_id=str(self.id.value),
                customer_id=customer_id,
                total=self.total.value,
            )
        )

    def fail_payment(self, reason: str) -> None:
        self.status = OrderStatus("payment_failed")
        self.raise_event(PaymentFailed(order_id=str(self.id.value), reason=reason))
```

Events accumulate internally until the `UnitOfWork` drains them on commit — you never call `get_events()` or `clear_events()` yourself in normal use.

---

## ✨ Auto-collection by repositories

This is the EF Core-inspired design at the heart of Hike's event system. Every repository (`save`, `update`, `upsert`) automatically calls `_collect_events(aggregate)` after writing, which:

1. Transfers the aggregate's `_events` into the repo's internal pending queue.
2. Clears the aggregate's `_events` so the same events are never dispatched twice.

The `UnitOfWork` then drains the pending queue when you call `uow.commit()`. **You never manually manage event collection.** The only decision you make is *what to do with the events* — which is the dispatch mode.

---

## Choosing a dispatch mode

Hike provides exactly two modes, configured via the `UnitOfWork`:

| Mode | Parameter | When to use |
| :--- | :--- | :--- |
| **In-memory synchronous** ⚡ | `bus=` | Reactions run in the same process; handler failure rolls back the commit |
| **Outbox → Inbox** 📬 | `outbox=` | Reactions run in a separate service; need crash safety |

---

## Mode 1: In-memory synchronous

Pass a bus to `uow(...)` with `bus=`. The UoW dispatches all collected events to the bus's subscribers **before** the database commit. If any handler raises an exception, the commit is aborted and the transaction rolls back — giving you strong consistency between your domain change and its reaction.

```python
from hike import InMemoryEventBus, UnitOfWork
from hike.persistence.providers.in_memory import InMemoryDBContext, InMemoryRepository

# --- set up ---
bus = InMemoryEventBus()
context = InMemoryDBContext()
repo: InMemoryRepository[UUID, Order] = InMemoryRepository()
uow = UnitOfWork(context)

# --- subscribe handlers ---
def send_confirmation_email(event: OrderPlaced) -> None:
    print(f"Sending email for order {event.order_id}")

def decrement_inventory(event: OrderPlaced) -> None:
    print(f"Decrementing stock for order {event.order_id}")

bus.subscribe(OrderPlaced, send_confirmation_email)
bus.subscribe(OrderPlaced, decrement_inventory)

# --- use ---
order = Order(status=OrderStatus("draft"), total=Price(49.99))
order.place(customer_id="user-42")

with uow(repo, bus=bus):
    repo.save(order)
    uow.commit()
    # ① bus.publish(OrderPlaced) → send_confirmation_email runs
    #                              → decrement_inventory runs
    # ② if all handlers succeed → db.commit()
    # ③ if any handler raises   → exception propagates, db.rollback()
```

### Two handler forms

`subscribe()` accepts either a plain **callable** or a **handler object** that subclasses `EventHandler[TEvent]`. Both work identically at dispatch time — the bus calls each as `handler(event)`.

```python
from hike import EventHandler

# Form 1 — callable (function, lambda, or any callable):
bus.subscribe(OrderPlaced, send_confirmation_email)
bus.subscribe(OrderPlaced, lambda e: print(f"Order: {e.order_id}"))

# Form 2 — handler object:
class SendConfirmationEmail(EventHandler[OrderPlaced]):
    def handle(self, event: OrderPlaced) -> None:
        print(f"Sending email for order {event.order_id}")

bus.subscribe(OrderPlaced, SendConfirmationEmail())
```

**When to use a handler object instead of a callable:**

- The handler needs **injected dependencies** (a mailer, a logger, a repository) — store them in `__init__` and use them in `handle`.
- The handler is complex enough to warrant its own class and tests.
- You want to enforce that all handlers for a given event share a common interface.

Both forms may be subscribed to the same event — the bus dispatches them in subscription order.

### Sequence of events

```
order.place()             → OrderPlaced appended to order._events
repo.save(order)          → repo collects OrderPlaced, clears order._events
uow.commit():
  ├─ bus.publish_all()    → handlers run synchronously, in order subscribed
  │     if any raises     → exception propagates; __exit__ calls rollback()
  └─ db.commit()          → order row written only if all handlers succeeded
```

### When a handler fails

#### The domain change is rolled back

This is the central guarantee of the in-memory mode. Because handlers run **before** `db.commit()`, any unhandled exception from a handler aborts the commit and rolls back the entire transaction. The domain change and the failed reaction are kept consistent — either both happen or neither does.

```python
def enforce_credit_limit(event: OrderPlaced) -> None:
    if event.total > customer.credit_limit:
        raise CreditLimitExceeded(event.customer_id)

bus.subscribe(OrderPlaced, enforce_credit_limit)

try:
    with uow(repo, bus=bus):
        order.place(customer_id="user-42")
        repo.save(order)
        uow.commit()
        # enforce_credit_limit raises CreditLimitExceeded
        # → db.commit() is never called
        # → __exit__ calls rollback()
        # → order does not exist in the database
except CreditLimitExceeded:
    ...   # handle the error at the application layer
```

#### Handlers run in subscription order; the first failure stops the chain

Multiple handlers on the same event run in the order they were subscribed. If handler A raises, handler B (subscribed afterward) **never runs**:

```python
bus.subscribe(OrderPlaced, send_confirmation_email)   # handler A — runs first
bus.subscribe(OrderPlaced, decrement_inventory)       # handler B — runs only if A succeeds
bus.subscribe(OrderPlaced, update_loyalty_points)     # handler C — runs only if A and B succeed
```

Likewise, if a single `uow.commit()` drains events from multiple aggregates, a failure on the first event's handler means no subsequent events are dispatched at all.

#### Suppressing errors inside a handler ("fire and forget")

If you want a handler to proceed regardless of failure — for example, to send a non-critical notification — catch the exception inside the handler itself. The `InMemoryEventBus` has no built-in error swallowing; it is entirely up to the handler:

```python
import logging

def send_marketing_email(event: OrderPlaced) -> None:
    try:
        mailer.send(event.customer_id, template="order_placed")
    except MailerUnavailable as exc:
        logging.warning("Marketing email skipped: %s", exc)
        # no re-raise → handler returns normally → chain continues
```

With this pattern, `send_marketing_email` can fail silently without rolling back the domain change or blocking subsequent handlers.

#### Log-and-reraise pattern

If you want to log the error and still roll back, re-raise after logging:

```python
def sync_search_index(event: OrderPlaced) -> None:
    try:
        search.index(event.order_id, event.total)
    except SearchServiceError as exc:
        logging.error("Search sync failed for order %s: %s", event.order_id, exc)
        raise   # re-raise → transaction rolls back
```

#### Summary

| Handler behaviour | Effect on subsequent handlers | Effect on DB commit |
| :--- | :--- | :--- |
| Returns normally | Next handler runs | Proceeds (if all handlers succeed) |
| Raises (unhandled) | Remaining handlers **do not run** | **Rolled back** |
| Catches exception, does not re-raise | Next handler runs | Proceeds normally |
| Catches exception, re-raises | Remaining handlers **do not run** | **Rolled back** |

#### Compensation — rolling back previously-succeeded handlers

When multiple handlers subscribe to the same event (or a batch of events), the bus dispatches them in order. If handler B fails after handler A has already succeeded, handler A's side effects are **not automatically reversed** by the database rollback — they may have committed their own transaction (e.g. a `CrossAggregateInvariantHandler` that updated another aggregate).

For this, `EventHandler` objects support a `compensate()` method. When any handler fails during `publish_all()`, the bus calls `compensate()` on every previously-succeeded handler object, in **reverse order**, before re-raising the original exception.

```python
from hike import EventHandler, CrossAggregateInvariantHandler

class IncrementHarborDockCount(CrossAggregateInvariantHandler[ShipLaunched]):
    def handle(self, event: ShipLaunched) -> None:
        with self._uow(self._repo):
            harbor = self._repo.get_one(self._harbor_id)
            harbor.receive_ship()   # dock_count += 1
            self._repo.update(harbor)
            self._uow.commit()

    def compensate(self, event: ShipLaunched) -> None:
        # Called if a later handler in the same publish_all() fails
        with self._uow(self._repo):
            harbor = self._repo.get_one(self._harbor_id)
            harbor.release_ship()   # dock_count -= 1
            self._repo.update(harbor)
            self._uow.commit()
```

**Rules:**

- `compensate()` is **abstract** — every `EventHandler` subclass must implement it, even if the body is just `pass`. This forces explicit acknowledgement of whether there is anything to undo.
- **Plain callables** (functions, lambdas) are not tracked and are never compensated.
- **Compensation order**: reverse of success order — the last succeeded handler is compensated first.
- **Compensation failures** are logged (`logging.exception`) but do not replace the original exception. All succeeded handlers are still compensated before the original error propagates.
- Compensation is **local** — it runs in-process, synchronously, within the same `publish_all()` call.

```python
bus.subscribe(ShipLaunched, IncrementHarborDockCount(harbor.id, harbor_repo, harbor_uow))
bus.subscribe(ShipLaunched, EnforceFuelCapacity(...))   # fails

# bus.publish_all([ShipLaunched(...)]):
#   1. IncrementHarborDockCount.handle()  → succeeds (harbor dock_count += 1)
#   2. EnforceFuelCapacity.handle()       → raises FuelCapacityExceeded
#   3. IncrementHarborDockCount.compensate() → harbor dock_count -= 1 (reversed)
#   4. FuelCapacityExceeded propagates
```

### Multiple repos, single bus

If you save aggregates from two repos in the same block, both repos' events are collected and dispatched together before commit:

```python
with uow(order_repo, inventory_repo, bus=bus):
    order.place(customer_id="user-42")
    order_repo.save(order)

    inventory.reserve(order.id)
    inventory_repo.update(inventory)

    uow.commit()
    # All events from both repos → dispatched before commit
    # All handlers succeed → both writes committed atomically
```

### `auto_commit` shorthand

If you always call `uow.commit()` at the end of your block, you can skip writing it explicitly:

```python
with uow(repo, bus=bus, auto_commit=True):
    order.place(customer_id="user-42")
    repo.save(order)
# handlers run, then commit fires automatically on block exit
```

### When to use in-memory sync

- Reactions live in the same Python process (same service, same thread).
- You want handler failures to prevent the domain change from being committed.
- You want simplicity — no extra database tables or background threads.

---

## `CrossAggregateInvariantHandler` — enforcing rules across aggregates

When a domain event triggers a check or update on a **second, different aggregate**, use `CrossAggregateInvariantHandler`. It is a handler object that receives the repository and unit-of-work for the *other* aggregate at construction time, so `handle()` can load, check, and update it in its own transaction.

```python
from hike import CrossAggregateInvariantHandler, UnitOfWork, IRepository

class IncrementHarborDockCount(CrossAggregateInvariantHandler[ShipLaunched]):
    """On ShipLaunched, update the Harbor aggregate that owns the berth."""

    def __init__(
        self,
        harbor_id: HarborID,
        repo: IRepository,
        uow: UnitOfWork,
    ) -> None:
        super().__init__(repo, uow)        # stores as self._repo / self._uow
        self._harbor_id = harbor_id

    def handle(self, event: ShipLaunched) -> None:
        with self._uow(self._repo):
            harbor = self._repo.get_one(self._harbor_id)
            CapacityRule().check(HarborCtx(harbor=harbor))   # optional invariant check
            harbor.receive_ship()
            self._repo.update(harbor)
            self._uow.commit()

    def compensate(self, event: ShipLaunched) -> None:
        # Called automatically if a later handler fails in the same publish_all()
        with self._uow(self._repo):
            harbor = self._repo.get_one(self._harbor_id)
            harbor.release_ship()          # undo the dock_count increment
            self._repo.update(harbor)
            self._uow.commit()
```

Subscribing and wiring:

```python
harbor_repo = ...   # IRepository for Harbor
harbor_uow  = UnitOfWork(harbor_db_context)

handler = IncrementHarborDockCount(harbor.id, harbor_repo, harbor_uow)
ship_bus.subscribe(ShipLaunched, handler)

# When a ship is launched:
with ship_uow(ship_repo, bus=ship_bus):
    ship.launch()
    ship_repo.save(ship)
    ship_uow.commit()
    # ① ship_bus dispatches ShipLaunched
    # ② handler opens its own harbor_uow transaction
    # ③ handler loads Harbor, checks CapacityRule, increments dock count, commits
    # ④ ship_uow commits the ship write (if no error in ②–③)
```

### Transaction model

`CrossAggregateInvariantHandler` opens its **own** `UnitOfWork` transaction — separate from the one that raised the event. This means:

| Scenario | What happens |
| :--- | :--- |
| Handler's transaction succeeds | Both the ship write and the harbor update commit |
| Handler's transaction fails (exception) | Handler exception propagates; ship write is **rolled back** (pre-commit dispatch) |
| Ship write fails (exception in `uow.commit()`) | Harbor update already committed; ship write rolls back — brief inconsistency |

For guaranteed atomicity across both aggregates in a single transaction, use a `DomainService` instead (see [Cross-Aggregate Invariants](cross-aggregate-invariants.md)).

### When to use `CrossAggregateInvariantHandler`

- An event from one aggregate must trigger a **write** on another aggregate.
- The invariant is enforced **reactively** (eventual consistency is acceptable).
- You want the handler to be composable, testable, and named explicitly.

---

## Mode 2: 📬 Outbox → Inbox (reliable cross-service delivery)

The problem with in-memory dispatch is that if the process crashes **after** the database commit but **before** the bus dispatches, your events are lost forever. For reactions in other services — or for any event that must *definitely* be delivered — you need the **outbox pattern**.

### How it works

```
Producer service:
  uow.commit():
    ├─ INSERT INTO orders (...)     ┐
    └─ INSERT INTO hike_outbox (...)┘  same DB transaction → atomic

Background relay (same or separate process):
  loop:
    SELECT * FROM hike_outbox WHERE processed = false
    for each row:
      bus.publish(deserialize(row))   → send to Kafka / HTTP / in-memory
      DELETE FROM hike_outbox WHERE id = row.id

Receiving bounded context:
  InboxProcessor.process(event_id, event):
    IF already in hike_inbox → skip          (idempotency)
    INSERT INTO hike_inbox (event_id, ...)
    bus.publish(event) → handler runs
    UPDATE hike_inbox SET processed = true WHERE event_id = ...
```

The outbox and the domain change are committed **atomically**. Even if the process crashes immediately afterward, the relay will pick up the event on the next poll. The inbox prevents the same event from being processed twice.

### Step 1: register your events

Events that go through the outbox must be serializable. Decorate them with `@register_event`:

```python
from dataclasses import dataclass
from hike import DomainEvent, register_event

@register_event
@dataclass(frozen=True)
class OrderPlaced(DomainEvent):
    order_id: str
    customer_id: str
    total: float
```

`@register_event` stores the class in a global registry so `deserialize_event` can reconstruct it by name. **The decorator must be the outermost decorator** — apply it before `@dataclass`.

> **Field types**: all fields must be JSON-serializable primitives (`str`, `int`, `float`, `bool`). Use `str` for UUIDs (`str(uuid)`) rather than `UUID` objects, since JSON has no UUID type.

### Step 2: produce — write events atomically

```python
from hike.persistence.providers.sqlalchemy.outbox import SQLAlchemyOutboxRepository
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

engine = create_engine("postgresql+psycopg://user:pass@localhost/db")
SQLAlchemyOutboxRepository.create_tables(engine)   # creates hike_outbox, once at startup

session_factory = sessionmaker(bind=engine)
outbox_repo = SQLAlchemyOutboxRepository()

with uow(order_repo, outbox=outbox_repo):
    order.place(customer_id="user-42")
    order_repo.save(order)
    uow.commit()
    # INSERT INTO orders + INSERT INTO hike_outbox in the same transaction
```

### Step 3: relay — dispatch from outbox

The relay runs in a loop, reading pending outbox rows, publishing them, then deleting them:

```python
import threading
from hike import OutboxRelay, InMemoryEventBus

# The relay bus is your external transport — in-memory for local, or Kafka/Redis for real cross-service
relay_bus = InMemoryEventBus()
relay_bus.subscribe(OrderPlaced, lambda e: kafka_producer.send("order-events", e))

relay = OutboxRelay(outbox_repo, relay_bus)

# Run in a daemon thread so it doesn't block your app startup:
thread = threading.Thread(target=relay.start, kwargs={"interval": 1.0}, daemon=True)
thread.start()
```

`relay.start(interval=1.0)` polls every second. For a cloud deployment you might run the relay as a separate process or container.

### Step 4: consume — inbox for exactly-once processing

The receiving service reads from Kafka (or whichever transport you use) and hands events to `InboxProcessor`:

```python
from hike import InboxProcessor, InMemoryEventBus, deserialize_event
from hike.persistence.providers.sqlalchemy.inbox import SQLAlchemyInboxRepository
from sqlalchemy.orm import Session

engine = create_engine("postgresql+psycopg://user:pass@localhost/receiving-db")
SQLAlchemyInboxRepository.create_tables(engine)   # creates hike_inbox, once at startup

local_bus = InMemoryEventBus()
local_bus.subscribe(OrderPlaced, fulfill_order)
local_bus.subscribe(OrderPlaced, notify_warehouse)

inbox_repo = SQLAlchemyInboxRepository(session)
processor = InboxProcessor(inbox_repo, local_bus)

# Consumer loop — Kafka, Redis Streams, SQS, HTTP webhook, etc.
for message in kafka_consumer:
    event = deserialize_event(message.event_type, message.event_data)
    processor.process(event_id=message.message_id, event=event)
    kafka_consumer.commit()   # ack only after processing
```

`InboxProcessor.process` is idempotent: if `event_id` has already been processed, the call returns immediately without dispatching.

### 🧪 Testing Mode 2 without a database

Replace the SQLAlchemy repositories with the library-provided in-memory versions — no database or schema setup needed:

```python
from hike import InMemoryOutboxRepository, InMemoryInboxRepository

# Drop-in replacements for testing:
outbox_repo = InMemoryOutboxRepository()   # instead of SQLAlchemyOutboxRepository()
inbox_repo  = InMemoryInboxRepository()   # instead of SQLAlchemyInboxRepository(session)
```

Both work identically to the SQLAlchemy implementations. `InMemoryOutboxRepository` integrates with `uow(outbox=...)` and stores records in a plain dict. `InMemoryInboxRepository` integrates with `InboxProcessor` for exactly-once deduplication.

---

### Sequence of events (crash-safe)

```
Order service:
  order.place()              → OrderPlaced in order._events
  order_repo.save(order)     → repo collects OrderPlaced
  uow.commit():
    ├─ INSERT INTO orders
    └─ INSERT INTO hike_outbox  ← same transaction; crash here = no data loss

  [crash here? hike_outbox row survives in the database]

Relay (restarts after crash):
  SELECT * FROM hike_outbox  → finds the surviving row
  bus.publish(OrderPlaced)   → sends to Kafka
  DELETE FROM hike_outbox    → cleanup

Fulfillment service:
  for message in kafka:
    InboxProcessor.process("msg-id-123", event):
      is_processed("msg-id-123") → False
      INSERT INTO hike_inbox
      local_bus.publish(event) → fulfill_order runs
      UPDATE processed = true
    kafka.commit()

  [duplicate delivery? process() returns immediately — idempotent]
```

---

## Serialization reference

`@register_event`, `serialize_event`, and `deserialize_event` form the serialization layer used by the outbox and inbox:

```python
from hike import register_event, serialize_event, deserialize_event

@register_event
@dataclass(frozen=True)
class ShipDispatched(DomainEvent):
    shipment_id: str
    destination: str

# Serialize (used by outbox on save)
event = ShipDispatched(shipment_id="sh-1", destination="London")
event_type, json_data = serialize_event(event)
# event_type → "ShipDispatched"
# json_data  → '{"shipment_id": "sh-1", "destination": "London"}'

# Deserialize (used by relay and inbox consumer)
restored = deserialize_event("ShipDispatched", json_data)
assert isinstance(restored, ShipDispatched)
assert restored.destination == "London"
```

---

## `EventBus.drain()` — manual dispatch

For tests, scripts, or thin application layers that don't use the full UoW pipeline, `drain()` publishes and clears an aggregate's events in one call:

```python
bus = InMemoryEventBus()
bus.subscribe(OrderPlaced, record_audit_log)

order.place(customer_id="user-42")

bus.drain(order)
# equivalent to:
#   bus.publish_all(order.get_events())
#   order.clear_events()
```

This is useful when writing integration tests that exercise the aggregate directly:

```python
def test_order_placed_event_is_raised() -> None:
    bus = InMemoryEventBus()
    received: list[DomainEvent] = []
    bus.subscribe(OrderPlaced, received.append)

    order = Order(status=OrderStatus("draft"), total=Price(49.99))
    order.place(customer_id="user-42")

    bus.drain(order)

    assert len(received) == 1
    assert received[0].customer_id == "user-42"
```

---

## Implementing a custom `EventBus`

`InMemoryEventBus` is synchronous and in-process — great for development, tests, and same-service reactions. For production cross-service delivery, subclass `EventBus` and implement `subscribe` and `publish`:

```python
from hike import EventBus, DomainEvent
from collections.abc import Callable
import json

class KafkaEventBus(EventBus):
    def __init__(self, producer: KafkaProducer, topic: str) -> None:
        self._producer = producer
        self._topic = topic

    def subscribe[TEvent: DomainEvent](
        self,
        event_type: type[TEvent],
        handler: Callable[[TEvent], None] | EventHandler[TEvent],
    ) -> None:
        # Kafka is fire-and-forget from the producer side;
        # consumers subscribe separately via their Kafka consumer group.
        pass

    def publish(self, event: DomainEvent) -> None:
        from hike import serialize_event
        event_type, payload = serialize_event(event)
        self._producer.send(
            self._topic,
            value=json.dumps({"event_type": event_type, "data": payload}).encode(),
        )
```

Pass this as `bus=` on a UoW if you want direct post-commit publication, or as the bus argument to `OutboxRelay` for the relay pattern.

---

## Quick reference

```python
from hike import (
    DomainEvent,                      # base class for all events
    register_event,                   # @register_event: required for outbox serialization
    serialize_event,                  # (type_name, json_str) ← used by outbox
    deserialize_event,                # DomainEvent ← used by relay and inbox consumer
    EventBus,                         # abstract pub/sub interface
    EventHandler,                     # abstract handler object base class
    InMemoryEventBus,                 # synchronous, in-process bus
    CrossAggregateInvariantHandler,   # handler with repo+uow for the other aggregate
    IOutboxRepository,                # abstract outbox — extend for custom backends
    OutboxRecord,                     # infrastructure Aggregate for one hike_outbox row
    OutboxRelay,                      # polls outbox → publishes → deletes
    IInboxRepository,                 # abstract inbox — extend for custom backends
    InboxRecord,                      # infrastructure Aggregate[str] for one hike_inbox row
    InboxProcessor,                   # exactly-once dispatch for incoming events
    InMemoryOutboxRepository,         # in-memory outbox — drop-in for tests
    InMemoryInboxRepository,          # in-memory inbox — drop-in for tests
)

# SQLAlchemy concrete implementations (production):
from hike.persistence.providers.sqlalchemy.outbox import (
    SQLAlchemyOutboxRepository,
    OutboxModel,            # the ORM model (include in Base.metadata if needed)
)
from hike.persistence.providers.sqlalchemy.inbox import (
    SQLAlchemyInboxRepository,
    InboxModel,
)
```

---

## Decision guide

```
Does the reaction live in the same Python process?
├─ Yes → use bus= (InMemoryEventBus)
│         Handlers run before commit; a handler failure rolls back the domain change.
│         Zero infrastructure, strong consistency within the process.
│
└─ No (another service, another container):
   Does the event need to survive a process crash?
   ├─ Yes → use outbox= + OutboxRelay + InboxProcessor
   │         Events committed atomically with domain changes.
   │         At-least-once delivery; inbox deduplication ensures exactly-once.
   │
   └─ No (best-effort, loss acceptable) → use bus= with a network-aware EventBus
             (Custom Kafka/Redis bus — no outbox table needed, but no crash safety.)
```