# Sagas

!!! warning "🚧 Work in Progress"
    This page is actively being written. The API and examples are functional, but some sections may be incomplete or revised before the stable release.

Some workflows are too long to fit inside a single transaction. Shipping an order might start when a payment is received but only finish three days later when a warehouse confirms dispatch. In between, the system must remember where it is in the process and react correctly to each new event — even if the process crashes and restarts.

A **saga** (also called a *process manager*) solves this. It is a stateful object that coordinates a multi-step business process by listening for events, updating its own state, and deciding what to do next. When the process is done, the saga deletes itself.

---

## The problem sagas solve

Imagine shipping an order. The rule is:

> An order can only be shipped **after** it has been submitted *and* billed.

The naive approach — a single handler that checks both conditions — is too fragile:

```python
class FulfillOrder(IEventHandler[OrderBilled]):
    def handle(self, event: OrderBilled) -> None:
        order = order_repo.get_one(event.order_id)
        if order.is_submitted and order.is_billed:   # ← where is is_submitted stored?
            ship(order)
```

You either mutate the `Order` aggregate with fulfillment state that doesn't belong there, or you create a separate table for tracking process progress — which is exactly what a saga is, done explicitly.

A saga gives this cross-event coordination a dedicated home:

- It is created by the first relevant event.
- It accumulates state as subsequent events arrive.
- It acts when all conditions are met.
- It deletes itself when the process is complete.

---

## Anatomy of a saga

Every saga has three pieces:

**State** — a plain dataclass that holds everything the saga needs to remember between events. It is persisted automatically between messages.

**Handlers** — methods that react to incoming events. Each handler receives the event, the current state, and a context object for publishing or scheduling timeouts. The framework loads the right state before calling the handler and saves it afterward.

**Correlation** — a declaration of how to match an incoming event to an existing saga instance. For example: "look up the saga whose `order_id` equals the event's `order_id`".

---

## Defining saga state

Saga state is a dataclass that inherits from `SagaData`. Every field must have a default so the framework can create a fresh instance when a saga starts:

```python
from dataclasses import dataclass
from hike.events.saga import SagaData

@dataclass(eq=False)
class ShippingPolicyData(SagaData):
    order_id: str = ""
    is_order_billed: bool = False
    is_order_submitted: bool = False
```

`SagaData` provides:

- `id: UUID` — auto-generated unique identifier for this saga instance.
- `completed: bool` — set to `True` by `mark_as_complete()`; triggers deletion by the framework.

> Use `@dataclass(eq=False)` on every `SagaData` subclass. Without it, the dataclass decorator generates field-based `__eq__` and sets `__hash__ = None`, which breaks identity-based persistence.

---

## Defining the saga class

```python
from dataclasses import dataclass
from hike.events.saga import (
    Saga, SagaContext, SagaMapper,
    started_by, handles, timeout_handler,
)

class ShippingPolicy(Saga[ShippingPolicyData]):

    def configure_how_to_find_saga(self, mapper: SagaMapper[ShippingPolicyData]) -> None:
        mapper.map_saga("order_id") \
            .to_message(OrderBilled, "order_id") \
            .to_message(OrderSubmitted, "order_id") \
            .to_message(OrderShipped, "order_id")

    @started_by
    def on_order_billed(self, msg: OrderBilled, ctx: SagaContext) -> None:
        self.data.order_id = msg.order_id
        self.data.is_order_billed = True
        self._try_ship(ctx)

    @started_by
    def on_order_submitted(self, msg: OrderSubmitted, ctx: SagaContext) -> None:
        self.data.order_id = msg.order_id
        self.data.is_order_submitted = True
        self._try_ship(ctx)

    @handles
    def on_order_shipped(self, msg: OrderShipped, ctx: SagaContext) -> None:
        self.mark_as_complete()

    def _try_ship(self, ctx: SagaContext) -> None:
        if self.data.is_order_billed and self.data.is_order_submitted:
            ctx.publish(ShipOrder(order_id=self.data.order_id))
```

### The three decorators

| Decorator | What happens when no existing saga is found |
| :--- | :--- |
| `@started_by` | A fresh `SagaData` instance is created automatically. |
| `@handles` | `SagaNotFoundError` is raised — the saga must already exist. |
| `@timeout_handler` | Receives a user-defined state object (not an event); see [Timeouts](#timeouts). |

The **message type** is inferred from the second parameter's type annotation — you never declare it explicitly.

### `configure_how_to_find_saga`

This method tells the framework how to locate an existing saga instance when a message arrives. `map_saga("order_id")` names the field on `SagaData` to match against; each `.to_message(EventType, "field")` names the field on the incoming event.

When `OrderBilled` arrives, the framework queries the repository for a `ShippingPolicyData` whose `order_id` equals `event.order_id`. If one exists it is loaded; if not, and the handler is `@started_by`, a new one is created.

### `self.data`

Inside every handler, `self.data` holds the current `ShippingPolicyData`. Mutate it freely — the framework persists it after the handler returns.

### `mark_as_complete()`

Call this inside any handler to signal that the saga is finished. After the handler returns, the framework deletes the saga data from storage.

---

## Wiring up

A `SagaManager` connects the saga to the event delivery infrastructure. Give it the saga class and a repository to store saga state. Call `subscribe_to` to register it with a subscriber for every event type the saga handles:

```python
from hike.events.saga import SagaManager
from hike.persistence.providers.in_memory import InMemoryPersistableRepository
from uuid import UUID

repo: InMemoryPersistableRepository[UUID, ShippingPolicyData] = (
    InMemoryPersistableRepository()
)
repo.session = {}

manager = SagaManager(ShippingPolicy, repo)
manager.inject_subscribers_to(subscriber)
```

`SagaManager.handle(event)` dispatches to the correct handler based on the event type. Use it anywhere you would use a regular event handler.

### What happens per event

```
OrderBilled arrives:
  1. Look up correlation rule: saga.order_id == event.order_id
  2. Query repository for matching ShippingPolicyData
  3. None found → @started_by → create ShippingPolicyData()
  4. Instantiate ShippingPolicy, inject data
  5. Call on_order_billed(event, ctx)
  6. Data not complete → repo.save(data)

OrderSubmitted arrives for the same order:
  1–2. Same lookup
  3. Found → load existing ShippingPolicyData
  4–5. Call on_order_submitted(event, ctx)
  6. Both flags set → _try_ship publishes ShipOrder
  7. Data not complete → repo.update(data)

OrderShipped arrives:
  1–4. Same
  5. Call on_order_shipped(event, ctx) → mark_as_complete()
  6. data.completed is True → repo.delete(data)
```

---

## Timeouts

A saga can schedule a future callback to itself. This is useful for escalation, retries, or expiration logic:

```python
from dataclasses import dataclass
from datetime import timedelta
from hike.events.saga import SagaData, Saga, SagaContext, SagaMapper, started_by, timeout_handler

@dataclass
class EscalationTimeout:
    order_id: str

@dataclass(eq=False)
class OrderApprovalData(SagaData):
    order_id: str = ""
    approved: bool = False

class OrderApprovalSaga(Saga[OrderApprovalData]):

    def configure_how_to_find_saga(self, mapper: SagaMapper[OrderApprovalData]) -> None:
        mapper.map_saga("order_id").to_message(OrderSubmittedForApproval, "order_id")

    @started_by
    def on_submitted(self, msg: OrderSubmittedForApproval, ctx: SagaContext) -> None:
        self.data.order_id = msg.order_id
        ctx.request_timeout(EscalationTimeout(msg.order_id), delay=timedelta(hours=24))

    @timeout_handler
    def on_escalation(self, state: EscalationTimeout, ctx: SagaContext) -> None:
        if not self.data.approved:
            ctx.publish(OrderEscalated(order_id=state.order_id))
        self.mark_as_complete()
```

### How timeouts work

`ctx.request_timeout(state, delay)` persists a `SagaTimeout` record containing the state object and the target `fire_at` timestamp. A `TimeoutManager` (which implements `IBackgroundTasks`) runs in a background thread. Rather than polling at a fixed interval, it sleeps until the soonest pending timeout fires (capped at `poll_interval` when no timeouts are pending), so it reacts immediately rather than waiting for the next poll tick. When a record's `fire_at` is in the past, it routes the timeout to the correct saga manager which calls the matching `@timeout_handler`.

The type of `state` determines which `@timeout_handler` is called — each handler must annotate its second parameter with the specific timeout state type it handles.

### Setting up `TimeoutManager`

`TimeoutManager` implements `IBackgroundTasks`, the same interface used by broker subscribers. Hand its task to a daemon thread:

```python
import threading
from hike.events.saga import TimeoutManager, SagaTimeout
from hike.persistence.providers.in_memory import InMemoryPersistableRepository
from uuid import UUID

timeout_repo: InMemoryPersistableRepository[UUID, SagaTimeout] = (
    InMemoryPersistableRepository()
)
timeout_repo.session = {}

manager = SagaManager(
    OrderApprovalSaga,
    saga_repo,
    publisher=publisher,
    timeout_repo=timeout_repo,
)

tm = TimeoutManager(timeout_repo, poll_interval=1.0)
tm.register(manager)

thread = threading.Thread(target=tm.tasks()[0], daemon=True)
thread.start()
```

---

## Compensation

Some business processes must be reversed when a later step fails. This is the **saga compensation** pattern — directly inspired by NServiceBus saga handling.

The idea: each forward step (a `@started_by` or `@handles` handler) optionally has a paired compensating action. When the saga needs to roll back, it calls `self.compensate(ctx)`, which runs all registered compensators in **reverse order** of the steps that have already completed.

### The `@compensates` decorator

Attach `@compensates(forward_handler)` to a method to register it as the compensator for that forward handler:

```python
from hike.events.saga import compensates

class BookingFlowSaga(Saga[BookingFlowData]):

    @started_by
    def on_booking_started(self, msg: BookingStarted, ctx: SagaContext) -> None:
        ctx.publish(ReserveFlight(booking_id=msg.booking_id))

    @compensates(on_booking_started)
    def cancel_flight(self, ctx: SagaContext) -> None:
        ctx.publish(CancelFlight(flight_id=self.data.flight_reservation_id))

    @handles
    def on_flight_reserved(self, msg: FlightReserved, ctx: SagaContext) -> None:
        self.data.flight_reservation_id = msg.reservation_id
        ctx.publish(BookHotel(booking_id=msg.booking_id))

    @compensates(on_flight_reserved)
    def cancel_hotel(self, ctx: SagaContext) -> None:
        ctx.publish(CancelHotel(hotel_id=self.data.hotel_reservation_id))

    @handles
    def on_hotel_booking_failed(self, msg: HotelBookingFailed, ctx: SagaContext) -> None:
        self.compensate(ctx)        # runs: cancel_hotel → cancel_flight (LIFO)
        self.mark_as_complete()

    @handles
    def on_hotel_booked(self, msg: HotelBooked, ctx: SagaContext) -> None:
        self.data.hotel_reservation_id = msg.reservation_id
        self.mark_as_complete()
```

Rules for `@compensates`:

- The forward handler must be **defined before** its compensator in the class body — Python evaluates class bodies top-to-bottom.
- The compensation method receives `self` and `ctx` only. The original message is not available; use `self.data` for any state the compensator needs.
- The framework raises `TypeError` at **class-definition time** if the referenced forward handler does not exist or is not a `@started_by` / `@handles` handler.
- Timeout handlers (`@timeout_handler`) cannot be compensated — they do not record to `completed_steps`.

### `self.compensate(ctx)` method

Calling `self.compensate(ctx)` inside any handler triggers the compensation chain:

1. Reads `self.data.completed_steps` — the list of forward handler names that have run successfully for this saga instance.
2. Iterates in **reverse** (LIFO), calling the registered compensator for each step that has one.
3. Steps with no `@compensates` counterpart are silently skipped.

```python
@handles
def on_payment_failed(self, msg: PaymentFailed, ctx: SagaContext) -> None:
    self.compensate(ctx)     # undoes every completed step in reverse
    self.mark_as_complete()  # clean up the saga
```

### How step tracking works

The framework automatically records which handlers have run. Every time a `@started_by` or `@handles` handler completes **successfully** (does not throw), its name is appended to `self.data.completed_steps`. This list is persisted with the saga data, so it survives restarts.

If a handler throws, the step is **not** recorded and the event buffered by `ctx.publish()` is discarded — the saga stays in its previous state.

### Deferred event delivery — the atomicity guarantee

`ctx.publish(event)` does **not** dispatch immediately. Events are buffered during handler execution and only sent to the publisher **after** the saga data has been successfully persisted. If the handler throws, the buffer is discarded.

This matches NServiceBus's outbox-within-saga behaviour: events sent inside a saga handler are only delivered if the handler succeeds and its state changes are durable.

```
Handler runs:
  ctx.publish(SomeEvent())   → buffered, not yet sent
  self.data.field = value    → in-memory mutation

After handler returns:
  1. saga_data.completed_steps updated
  2. repo.save / repo.update / repo.delete
  3. publisher.publish(buffer)   ← sent only after persist succeeds
```

If the publisher call fails (e.g. broker is down), the saga data is already persisted. You can make delivery crash-safe by passing an `OutboxEventPublisher` as the `publisher=` argument to `SagaManager`.

---

## Publishing events from a saga

`ctx.publish(event)` buffers an integration event for delivery after the current handler persists. Pass a publisher when constructing the manager:

```python
manager = SagaManager(
    ShippingPolicy,
    repo,
    publisher=my_publisher,       # any IExternalEventPublisher implementation
)
```

If events are buffered but no publisher is configured, a `RuntimeError` is raised after the handler returns (at flush time).

---

## Saga state storage

`SagaData` implements `Persistable[UUID]`, which means it plugs into any existing `IRepository` provider — in-memory, SQLAlchemy, pymongo, Redis. No new repository type is needed:

```python
# In-memory (testing / development)
from hike.persistence.providers.in_memory import InMemoryPersistableRepository

repo = InMemoryPersistableRepository()
repo.session = {}

# Any other provider works identically
# from hike.persistence.providers.sqlalchemy import SQLAlchemyPersistableRepository
# repo = SQLAlchemyPersistableRepository(session)
```

`SagaTimeout` is also a `Persistable[UUID]`, so timeout records use the same infrastructure.

---

## `SagaNotFoundError`

When a `@handles` method receives an event and no matching saga exists, `SagaNotFoundError` is raised:

```python
from hike.events.saga import SagaNotFoundError

try:
    manager.handle(OrderShipped(order_id="nonexistent"))
except SagaNotFoundError as exc:
    print(exc.event_type)   # OrderShipped
    print(exc.event)        # the event that had no match
```

This usually means the saga-starting event was missed or arrived out of order. Depending on your infrastructure, you may want to requeue the message or send it to a dead-letter queue.

---

## Completion notification

`SagaManager` does not introduce any threading or synchronization primitives itself. Instead, pass an `on_complete` callback that is called with the saga's UUID when `mark_as_complete()` is triggered — from `handle()` or `handle_timeout()`. You own the synchronization mechanism, so the same manager works in threads, processes, or async code without change.

`SagaManager` also implements `IBackgroundTasks`. Its `tasks()` method delegates to the publisher's background tasks (broker connection loops, etc.), so you can hand the manager directly to any lifecycle runner without unwrapping the publisher separately.

### Fire and forget (background saga)

Start the manager's background tasks in daemon threads, subscribe it to incoming events, and register a lightweight callback for completion.

```python
import logging
import threading

log = logging.getLogger(__name__)

manager = SagaManager(
    ShippingPolicy,
    repo,
    publisher=my_external_publisher,
    on_complete=lambda saga_id: log.info("saga %s completed", saga_id),
)

# Run the publisher's broker connection loops in daemon threads.
for task in manager.tasks():
    threading.Thread(target=task, daemon=True).start()

manager.inject_subscribers_to(subscriber)
# The log line fires whenever any saga instance calls mark_as_complete().
```

### Halt until the saga completes

When a caller must block until the saga finishes — for example, in a synchronous HTTP handler or a test — use `threading.Event` as the completion signal while the manager runs in background threads:

```python
import threading

done = threading.Event()

manager = SagaManager(
    ShippingPolicy,
    repo,
    publisher=my_external_publisher,
    on_complete=lambda _: done.set(),
)

for task in manager.tasks():
    threading.Thread(target=task, daemon=True).start()

manager.handle(OrderPlaced(order_id="order-1"))

# Block until the saga calls mark_as_complete() or 30 seconds elapse.
if not done.wait(timeout=30.0):
    raise TimeoutError("saga did not complete in time")
```

For multiple concurrent saga instances each needing an independent signal, key the events by saga ID:

```python
import threading
from uuid import UUID

completion_events: dict[UUID, threading.Event] = {}

def on_complete(saga_id: UUID) -> None:
    if saga_id in completion_events:
        completion_events[saga_id].set()

manager = SagaManager(ShippingPolicy, repo, on_complete=on_complete)

manager.handle(OrderPlaced(order_id="A"))
saga_id_a = list(repo.session.keys())[0]
completion_events[saga_id_a] = threading.Event()

# ... events arrive for saga A ...

completed = completion_events[saga_id_a].wait(timeout=30.0)
```

---

## Multiple concurrent saga instances

Each correlation value produces an independent saga instance. `SagaManager` handles any number of concurrent instances of the same saga type without interference:

```python
manager.handle(OrderPlaced(order_id="A"))   # creates saga instance for order A
manager.handle(OrderPlaced(order_id="B"))   # creates saga instance for order B

# Events for A and B are routed independently
manager.handle(OrderBilled(order_id="A"))   # only saga A sees this
manager.handle(OrderShipped(order_id="A"))  # saga A completes; saga B still running
```

Multiple saga *types* also coexist without interference — give each type its own `SagaManager` and its own repository:

```python
shipping_manager = SagaManager(ShippingPolicy, shipping_repo)
billing_manager  = SagaManager(BillingPolicy, billing_repo)

# Both managers can handle the same OrderPlaced event independently
shipping_manager.handle(OrderPlaced(order_id="order-1"))
billing_manager.handle(OrderPlaced(order_id="order-1"))
```

---

## Aggregate locking

When a saga touches aggregates across multiple steps, a concurrent process could read or mutate those aggregates between steps. Use `ctx.lock(repo, aggregate_id)` inside a handler to acquire an exclusive **semantic lock** on an aggregate for the lifetime of the saga:

```python
@started_by
def on_order_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
    self.data.order_id = msg.order_id
    # Lock the order aggregate for the entire saga lifetime.
    ctx.lock(order_repo, msg.order_id)
```

The lock is:

- **Held for the saga's lifetime.** It is automatically released when the saga completes (`mark_as_complete()`) or when any handler raises an exception.
- **Reentrant.** The same saga can call `ctx.lock()` on the same aggregate in multiple steps without deadlocking.
- **Cross-step.** A lock acquired in step 1 is still held in step 2, step 3, and so on.
- **Conflict-raising.** If another process already holds the lock, `LockConflictError` is raised after the optional `timeout` elapses.

```python
@started_by
def on_order_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
    self.data.order_id = msg.order_id
    ctx.lock(order_repo, msg.order_id, timeout=5.0)  # wait up to 5 s
```

### How locking is implemented per provider

Each `IRepository` provider implements `acquire_lock` and `release_lock` in its own way:

| Provider | Mechanism |
| :--- | :--- |
| `InMemoryRepository` | `threading.Condition` — blocks in-process threads |
| `SQLAlchemyRepository` | A shared `__hike_saga_locks` table with `INSERT … ON CONFLICT` |
| `PyMongoRepository` | A dedicated `<collection>__hike_locks` collection with `insert_one` + unique index |
| `RedisRepository` | `SET NX` with a Lua atomic `DEL` for release |

**Lock TTL on persistent backends.** Locks in `SQLAlchemyRepository`, `PyMongoRepository`, and `RedisRepository` are stored records that persist across process restarts. A TTL (time-to-live) prevents orphaned locks when a process crashes before calling `release_lock`. Each provider accepts a `lock_ttl` constructor parameter (default 300 seconds):

```python
repo = SQLAlchemyRepository(MyAggregate, lock_ttl=120.0)  # 2-minute TTL
```

`InMemoryRepository` accepts `lock_ttl=None` (default) since in-memory state is cleared on process exit.

You can also acquire and release locks directly on any repository without a saga:

```python
# Acquire indefinitely
order_repo.acquire_lock(order_id, owner="my-process")
order_repo.release_lock(order_id, owner="my-process")

# Context manager (acquire on enter, release on exit — even on error)
with order_repo.locked(order_id, owner="my-process", timeout=5.0):
    order = order_repo.get_one(order_id)
    order.start_fulfillment()
    order_repo.update(order)
```

### `ctx.saga_id`

Inside a handler, `ctx.saga_id` exposes the current saga instance's UUID. The saga manager uses this ID as the lock owner, so all locks acquired by a saga are associated with the same token and released together.

---

## Complete example

```python
from __future__ import annotations

import threading
from dataclasses import dataclass
from uuid import UUID

from hike.events.saga import (
    Saga, SagaContext, SagaData, SagaManager, SagaMapper,
    handles, started_by,
)
from hike.persistence.providers.in_memory import InMemoryPersistableRepository


# ── Events ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class OrderBilled:
    order_id: str
    id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class OrderSubmitted:
    order_id: str
    id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class OrderShipped:
    order_id: str
    id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class ShipOrder:
    order_id: str


# ── Saga ──────────────────────────────────────────────────────────────────────

@dataclass(eq=False)
class ShippingPolicyData(SagaData):
    order_id: str = ""
    is_order_billed: bool = False
    is_order_submitted: bool = False


class ShippingPolicy(Saga[ShippingPolicyData]):

    def configure_how_to_find_saga(self, mapper: SagaMapper[ShippingPolicyData]) -> None:
        mapper.map_saga("order_id")
            .to_message(OrderBilled, "order_id")
            .to_message(OrderSubmitted, "order_id")
            .to_message(OrderShipped, "order_id")

    @started_by
    def on_order_billed(self, msg: OrderBilled, ctx: SagaContext) -> None:
        self.data.order_id = msg.order_id
        self.data.is_order_billed = True
        self._try_ship(ctx)

    @started_by
    def on_order_submitted(self, msg: OrderSubmitted, ctx: SagaContext) -> None:
        self.data.order_id = msg.order_id
        self.data.is_order_submitted = True
        self._try_ship(ctx)

    @handles
    def on_order_shipped(self, msg: OrderShipped, ctx: SagaContext) -> None:
        self.mark_as_complete()

    def _try_ship(self, ctx: SagaContext) -> None:
        if self.data.is_order_billed and self.data.is_order_submitted:
            ctx.publish(ShipOrder(order_id=self.data.order_id))


# ── Wire up ───────────────────────────────────────────────────────────────────

saga_repo: InMemoryPersistableRepository[UUID, ShippingPolicyData] = (
    InMemoryPersistableRepository()
)
saga_repo.session = {}

manager = SagaManager(ShippingPolicy, saga_repo, publisher=my_external_publisher)

for task in manager.tasks():
    threading.Thread(target=task, daemon=True).start()

manager.inject_subscribers_to(subscriber)

# ── Usage ─────────────────────────────────────────────────────────────────────

# Events can arrive in any order. The saga handles both cases.

# Case 1: billed first, then submitted
subscriber.handle(OrderBilled(order_id="order-1"))  # saga created, waiting
subscriber.handle(OrderSubmitted(order_id="order-1"))  # saga fires ShipOrder
subscriber.handle(OrderShipped(order_id="order-1"))  # saga completes, deleted

# Case 2: submitted first, then billed
subscriber.handle(OrderSubmitted(order_id="order-2"))
subscriber.handle(OrderBilled(order_id="order-2"))  # saga fires ShipOrder
subscriber.handle(OrderShipped(order_id="order-2"))
```

---

## Design notes

**Saga state is framework-managed.** You never call `repo.save()` or `repo.delete()` yourself inside a handler. The framework saves, updates, or deletes after each handler returns based on whether the saga is new or complete.

**Correlation is declared once.** `configure_how_to_find_saga` is called once when `SagaManager` is constructed (not per-message), so the correlation map is built eagerly and reused for every event.

**Any repository provider works.** Because `SagaData` implements `Persistable`, it works with every `IRepository` provider (in-memory, SQLAlchemy, pymongo, Redis) without any provider-specific saga code.

**Multiple saga types coexist.** Create one `SagaManager` per saga class and register each independently with the subscriber. Sagas of different types never interfere.

---

## Quick reference

```python
from hike.events.saga import (
    Saga,             # abstract base — inherit as Saga[YourDataClass]
    SagaData,         # base for state bags — subclass with @dataclass(eq=False)
    SagaContext,      # passed to every handler; .publish() and .request_timeout()
    SagaMapper,       # type annotation for configure_how_to_find_saga parameter
    SagaManager,      # orchestrates event → saga routing
    TimeoutManager,   # background thread that delivers expired timeouts
    SagaTimeout,      # internal timeout record; store in IRepository
    SagaNotFoundError,# raised by @handles when no saga instance exists
    started_by,       # decorator: create new saga if not found
    handles,          # decorator: error if not found
    timeout_handler,  # decorator: called by TimeoutManager
    compensates,      # decorator: link a compensator to a forward handler
)
```

| Question | Answer |
| :--- | :--- |
| How is state persisted? | Any `IRepository[UUID, YourSagaData, ...]` provider |
| How are events correlated? | `configure_how_to_find_saga` maps saga fields ↔ event fields |
| What starts a saga? | The first `@started_by` event to match a correlation |
| What ends a saga? | Calling `self.mark_as_complete()` inside any handler |
| How are timeouts delivered? | `TimeoutManager` (implements `IBackgroundTasks`) sleeps until the soonest pending timeout, then delivers it |
| Can a saga publish events? | Yes — `ctx.publish(event)`, deferred until after persist; requires `publisher=` on `SagaManager` |
| How does compensation work? | `@compensates(handler)` + `self.compensate(ctx)` — runs compensators LIFO |
| How to react when a saga finishes? | Pass `on_complete=callback` to `SagaManager`; use `threading.Event` to block, or a plain function to fire-and-forget. Run `manager.tasks()` in daemon threads to start the publisher's background loops |
| Can multiple instances run at once? | Yes — each correlation value creates an independent instance; `on_complete` receives the completed instance's UUID |
| How to lock an aggregate for a saga? | `ctx.lock(repo, aggregate_id, timeout=5.0)` — held for saga lifetime, auto-released on completion or failure |
| How to get the saga's own ID? | `ctx.saga_id` — returns the `UUID` of the current saga instance |
