# Domain Events

When something important happens in your domain — an order is placed, a payment fails, a user upgrades their subscription — that fact deserves a name and a type. **Domain events** give meaningful occurrences a permanent, typed record of *what happened*, and let other parts of the system react to them without being tightly coupled to the code that triggered them.

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

An event describes something that *has already happened*, so its name is always in the past tense. Events are immutable — use a frozen dataclass:

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

Every `DomainEvent` automatically gets:

| Field | Type | Default |
| :--- | :--- | :--- |
| `id` | `UUID` | auto-generated `uuid4()` |
| `occurred_at` | `float` | `time.time()` at instantiation |

And these class methods:

- `event_type()` → the class name as a string (used as the routing key by brokers).
- `to_dict()` / `from_dict()` → used for serialization by the outbox and brokers.

> **Keep events small.** An event should carry only what a subscriber needs to react — IDs and key values. If a subscriber needs the full aggregate, it re-fetches it from the repository using the ID from the event.

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

    @command
    def place(self, customer_id: str) -> None:
        self.status = OrderStatus("placed")
        self.raise_event(
            OrderPlaced(
                order_id=str(self.id.value),
                customer_id=customer_id,
                total=self.total.value,
            )
        )
```

Events accumulate internally on `order._events` until the `UnitOfWork` drains them on commit. You never call `get_events()` or `clear_events()` yourself in normal use.

---

## Auto-collection by repositories

This is the EF Core-inspired design at the heart of Hike's event system. Every repository (`save`, `update`, `upsert`) automatically calls `_after_mutate(aggregate)`, which transfers the aggregate's `_events` into the repository's internal pending queue and clears the aggregate's own list so events are never dispatched twice.

The `UnitOfWork` then calls `drain_events()` on each repository when you call `uow.commit()`. **You never manually manage event collection.**

---

## The `EventBus` — wiring publishers to subscribers

`EventBus` is an in-memory, synchronous publish/subscribe bus. Create one, attach handlers, and pass it to `UnitOfWork` at construction time:

```python
from hike import UnitOfWork
from hike.events.event_bus import EventBus
from hike.persistence.providers.in_memory import InMemoryDBContext, InMemoryRepository

bus = EventBus()
context = InMemoryDBContext()
uow = UnitOfWork(context, event_producer=bus)
```

On every `uow.commit()`:

```
repo.save(order)         → repo collects OrderPlaced from order._events
uow.commit():
  ├─ bus.publish(events) → all registered handlers run synchronously
  │     if any raises    → exception propagates, context.rollback() fires on exit
  └─ context.commit()    → database write commits only when all handlers succeed
```

This is the core transaction guarantee: **handlers run before the database commit**. A handler failure rolls back the entire unit of work.

---

## Subscribing handlers

`EventBus.subscribe()` accepts `IEventHandler` instances. The handler's event type is inferred automatically from its generic parameter:

```python
from hike.events.interfaces import IEventHandler

class SendConfirmationEmail(IEventHandler[OrderPlaced]):
    def handle(self, event: OrderPlaced) -> None:
        print(f"Sending email for order {event.order_id}")

class DecrementInventory(IEventHandler[OrderPlaced]):
    def handle(self, event: OrderPlaced) -> None:
        print(f"Decrementing stock for order {event.order_id}")

bus.subscribe(SendConfirmationEmail())
bus.subscribe(DecrementInventory())
```

Multiple handlers on the same event type are dispatched in subscription order.

### Handler form

`IEventHandler` objects can be called directly (they implement `__call__`), so you can use them wherever a callable is expected. They also support dependency injection via `__init__`:

```python
class SendConfirmationEmail(IEventHandler[OrderPlaced]):
    def __init__(self, mailer: Mailer) -> None:
        self._mailer = mailer

    def handle(self, event: OrderPlaced) -> None:
        self._mailer.send(event.customer_id, template="order_placed")

bus.subscribe(SendConfirmationEmail(mailer))
```

---

## Handler failure and the transaction guarantee

Because handlers run inside `commit()`, **before** `context.commit()`, any unhandled exception from a handler aborts the commit:

```python
class EnforceCreditLimit(IEventHandler[OrderPlaced]):
    def handle(self, event: OrderPlaced) -> None:
        if event.total > self._customer.credit_limit:
            raise CreditLimitExceeded(event.customer_id)

bus.subscribe(EnforceCreditLimit(customer_service))

try:
    with uow(repo):
        order.place(customer_id="user-42")
        repo.save(order)
        uow.commit()
        # EnforceCreditLimit raises CreditLimitExceeded
        # → context.commit() is never called
        # → __exit__ calls rollback()
        # → order does not exist in the database
except CreditLimitExceeded:
    ...
```

Handlers run in subscription order. If handler A raises, handler B (subscribed afterward) never runs.

### Suppressing errors inside a handler

If you want a handler to proceed regardless of failure, catch the exception inside the handler:

```python
class SendMarketingEmail(IEventHandler[OrderPlaced]):
    def handle(self, event: OrderPlaced) -> None:
        try:
            self._mailer.send(event.customer_id, template="welcome")
        except MailerUnavailable as exc:
            logging.warning("Marketing email skipped: %s", exc)
            # no re-raise → handler returns normally → chain continues
```

### Handler failure summary

| Handler behaviour | Effect on subsequent handlers | Effect on DB commit |
| :--- | :--- | :--- |
| Returns normally | Next handler runs | Proceeds (if all succeed) |
| Raises (unhandled) | Remaining handlers **do not run** | **Rolled back** |
| Catches, does not re-raise | Next handler runs | Proceeds normally |
| Catches, re-raises | Remaining handlers **do not run** | **Rolled back** |

---

## Reversible handlers — compensation

When multiple handlers run for the same event and handler B fails after handler A has already written its own transaction, handler A's side effects are **not automatically reversed** by the main rollback — they happened in a separate transaction.

For this, extend `IReversibleEventHandler` and implement `compensate()`:

```python
from hike.events.interfaces import IReversibleEventHandler

class IncrementHarborDockCount(IReversibleEventHandler[ShipLaunched]):
    def __init__(self, harbor_id, repo, uow) -> None:
        self._harbor_id = harbor_id
        self._repo = repo
        self._uow = uow

    def handle(self, event: ShipLaunched) -> None:
        with self._uow(self._repo):
            harbor = self._repo.get_one(self._harbor_id)
            harbor.receive_ship()        # dock_count += 1
            self._repo.update(harbor)
            self._uow.commit()

    def compensate(self) -> None:
        # Called automatically if a later handler in the same publish() fails
        with self._uow(self._repo):
            harbor = self._repo.get_one(self._harbor_id)
            harbor.release_ship()        # dock_count -= 1
            self._repo.update(harbor)
            self._uow.commit()
```

**How it works:**

```
bus.publish([ShipLaunched(...)]):
  1. IncrementHarborDockCount.handle()  → succeeds (dock_count += 1)
  2. EnforceFuelCapacity.handle()       → raises FuelCapacityExceeded
  3. IncrementHarborDockCount.compensate() → dock_count -= 1  (reversed)
  4. FuelCapacityExceeded propagates
```

**Rules:**

- The `EventBus` dispatches all `IReversibleEventHandler`s first, then regular `IEventHandler`s. A failure during regular handlers triggers compensation on all reversible handlers that already succeeded.
- Compensation runs in **reverse subscription order** — the last reversible handler that succeeded is compensated first.
- Compensation failures are logged but do not replace the original exception.

---

## Multiple repos in one unit of work

If you save aggregates from two repos in the same block, both repos' events are collected and dispatched together before commit:

```python
with uow(order_repo, inventory_repo):
    order.place(customer_id="user-42")
    order_repo.save(order)

    inventory.reserve(order.id)
    inventory_repo.update(inventory)

    uow.commit()
    # All events from both repos → dispatched before commit
    # All handlers succeed → both writes committed atomically
```

### `auto_commit` shorthand

```python
with uow(repo, auto_commit=True):
    order.place(customer_id="user-42")
    repo.save(order)
# commit fires automatically on clean block exit
```

---

## Testing — `bus.drain()`

For unit tests or scripts that don't use the full UoW pipeline, `drain()` publishes and clears an aggregate's events in one call:

```python
def test_order_placed_event_is_raised() -> None:
    bus = EventBus()
    received: list[DomainEvent] = []

    class Collect(IEventHandler[OrderPlaced]):
        def handle(self, event: OrderPlaced) -> None:
            received.append(event)

    bus.subscribe(Collect())

    order = Order(status=OrderStatus("draft"), total=Price(49.99))
    order.place(customer_id="user-42")
    bus.drain(order)

    assert len(received) == 1
    assert received[0].customer_id == "user-42"
```

`bus.drain(aggregate)` is equivalent to `bus.publish(aggregate.get_events()); aggregate.clear_events()`.

---

## Quick reference

```python
from hike import DomainEvent, UnitOfWork
from hike.events.event_bus import EventBus
from hike.events.interfaces import IEventHandler, IReversibleEventHandler

# Define an event
@dataclass(frozen=True)
class OrderPlaced(DomainEvent):
    order_id: str
    customer_id: str

# Define a handler
class SendEmail(IEventHandler[OrderPlaced]):
    def handle(self, event: OrderPlaced) -> None: ...

# Define a reversible handler (with compensation)
class IncrementCounter(IReversibleEventHandler[OrderPlaced]):
    def handle(self, event: OrderPlaced) -> None: ...
    def compensate(self) -> None: ...

# Wire up
bus = EventBus()
bus.subscribe(SendEmail())
bus.subscribe(IncrementCounter())

uow = UnitOfWork(context, event_producer=bus)

# Use
with uow(repo):
    aggregate.do_something()
    repo.save(aggregate)
    uow.commit()   # → handlers run → commit (or rollback on failure)
```

---

## When to use in-memory events vs. integration events

| Scenario | Use |
| :--- | :--- |
| Reactions live in the **same Python process** | `EventBus` — synchronous, strong consistency |
| Reactions cross a **service boundary** or need crash-safe delivery | See [Integration Events](integration-events.md) |
