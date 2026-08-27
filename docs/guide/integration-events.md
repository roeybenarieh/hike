# Integration Events

An **integration event** is a domain event that crosses a service boundary. Where [domain events](domain-events.md) are dispatched synchronously within a process, integration events must survive process crashes, network partitions, and message broker restarts, and be delivered at-least-once to one or more receiving services.

Hike implements integration events using the **transactional outbox/inbox pattern** via `TransactionalBox`.

---

## The problem: lost events

The naive approach — "commit the domain change, then publish to Kafka" — has a fatal race condition:

```
1. INSERT INTO orders ...    ← committed ✓
2. kafka_producer.send()     ← process crashes here ✗
```

The order exists in the database, but `OrderPlaced` was never delivered. The fulfillment service never received it. You've silently lost an event.

---

## The solution: transactional outbox/inbox

The fix is to write the event to the **same database transaction** as the domain change, then relay it to the broker from a background loop:

```
Producer:
  BEGIN TRANSACTION
    INSERT INTO orders (...)
    INSERT INTO outbox (event_type, data, ...)
  COMMIT                          ← atomic; crash here = no data loss

Outbox relay (background loop):
  SELECT * FROM outbox WHERE processed = false
  for each row:
    broker.publish(row)           ← send to Kafka / RabbitMQ / Redis
    DELETE FROM outbox WHERE id = row.id
    COMMIT

Consumer:
  for message in broker:
    if message.id IN inbox:       ← already processed; skip
      ack; continue
    INSERT INTO inbox (message.id, ...)
    local_bus.publish(event)      ← call handlers
    UPDATE inbox SET processed = true
    ack
```

If the relay crashes after publishing but before deleting the outbox row, it re-sends on the next poll — the inbox's deduplication makes that safe.

---

## `TransactionalBox`

`TransactionalBox` is Hike's combined outbox + inbox that plugs into `UnitOfWork` exactly like an `EventBus`:

```python
from hike import UnitOfWork
from hike.events.builder import TransactionalBoxBuilder

box = (
    TransactionalBoxBuilder()
    .outbox_repository(outbox_repo, outbox_uow)
    .inbox_repository(inbox_repo, inbox_uow)
    .with_kafka(producer=producer, consumer=consumer)
    .build()
)

# Subscribe handlers to the inbox side
from hike.events.interfaces import IEventHandler

class FulfillOrder(IEventHandler[OrderPlaced]):
    def handle(self, event: OrderPlaced) -> None: ...

box.subscribe(FulfillOrder())

# Pass the box as the event producer to UoW (outbox side)
uow = UnitOfWork(order_context, event_producer=box)
```

On `uow.commit()`, events are written to the outbox table in the **same transaction** as the domain change. The background relay then picks them up and forwards them to the broker. The inbox side receives from the broker, deduplicates, and dispatches to your handlers.

---

## Building a `TransactionalBox`

### Step 1 — choose storage for outbox and inbox

You need two repositories — one for the outbox (producer side) and one for the inbox (consumer side). They can live in the same database or separate ones.

For production, use a persistence provider's repository implementation. For testing, use the in-memory repositories from the provider:

```python
# Production (SQLAlchemy example):
from hike.persistence.providers.sqlalchemy import (
    InMemoryDBContext,  # replace with SQLAlchemyDBContext
    InMemoryRepository, # replace with SQLAlchemyRepository
)

# Testing (in-memory, no DB required):
from hike.persistence.providers.in_memory import (
    InMemoryDBContext,
    InMemoryRepository,
)
from hike import DomainEvent, UnitOfWork

outbox_context = InMemoryDBContext()
outbox_repo: InMemoryRepository[Any, DomainEvent] = InMemoryRepository()
outbox_uow = UnitOfWork(outbox_context)

inbox_context = InMemoryDBContext()
inbox_repo: InMemoryRepository[Any, DomainEvent] = InMemoryRepository()
inbox_uow = UnitOfWork(inbox_context)
```

### Step 2 — pick a broker

Choose one of the built-in broker shortcuts:

=== "Kafka"

    ```python
    from confluent_kafka import Producer, Consumer

    producer = Producer({"bootstrap.servers": "localhost:9092"})
    consumer = Consumer({
        "bootstrap.servers": "localhost:9092",
        "group.id": "fulfillment-service",
        "enable.auto.commit": "false",
    })

    box = (
        TransactionalBoxBuilder()
        .outbox_repository(outbox_repo, outbox_uow)
        .inbox_repository(inbox_repo, inbox_uow)
        .with_kafka(producer=producer, consumer=consumer, topic_prefix="myapp")
        .build()
    )
    ```

    Each event type is published to its own topic: `{topic_prefix}.{EventTypeName}`.
    Requires `pip install hike[kafka]`.

=== "RabbitMQ"

    ```python
    import pika

    connection = pika.BlockingConnection(pika.ConnectionParameters("localhost"))
    pub_channel = connection.channel()
    sub_channel = connection.channel()

    box = (
        TransactionalBoxBuilder()
        .outbox_repository(outbox_repo, outbox_uow)
        .inbox_repository(inbox_repo, inbox_uow)
        .with_rabbitmq(
            pub_channel=pub_channel,
            sub_channel=sub_channel,
            exchange="myapp.events",
        )
        .build()
    )
    ```

    Requires `pip install hike[rabbitmq]`.

=== "Redis Streams"

    ```python
    import redis

    client = redis.Redis(host="localhost", port=6379)

    box = (
        TransactionalBoxBuilder()
        .outbox_repository(outbox_repo, outbox_uow)
        .inbox_repository(inbox_repo, inbox_uow)
        .with_redis_broker(client=client, stream_prefix="myapp")
        .build()
    )
    ```

    Each event type uses its own stream: `{stream_prefix}.{EventTypeName}`.
    Requires `pip install hike[redis]`.

=== "Custom broker"

    Implement `IEventPublisher` and `IExternalEventSubscriber` for any transport, then wire them directly:

    ```python
    box = (
        TransactionalBoxBuilder()
        .outbox_repository(outbox_repo, outbox_uow)
        .inbox_repository(inbox_repo, inbox_uow)
        .broker_publisher(MyPublisher())
        .broker_subscriber(MySubscriber())
        .build()
    )
    ```

### Step 3 — register handlers and connect to UoW

```python
# Subscribe handlers (inbox side — receives from broker)
box.subscribe(FulfillOrder())
box.subscribe(NotifyWarehouse())

# Connect to UnitOfWork (outbox side — publishes on commit)
order_context = InMemoryDBContext()  # or your real DB context
uow = UnitOfWork(order_context, event_producer=box)
```

### Step 4 — run background tasks

`TransactionalBox` runs two background loops: the outbox relay and the broker consumer. Both are returned as callables from `box.tasks()`. Run each in a daemon thread or separate process:

```python
import threading

for task in box.tasks():
    t = threading.Thread(target=task, daemon=True)
    t.start()
```

The outbox relay polls the outbox repository, forwards events to the broker, then deletes them. The broker consumer reads from the broker, persists to the inbox for deduplication, and dispatches to your handlers via `box.subscribe(...)`.

---

## Producer — publishing events

On the producer side, domain events flow into the outbox automatically via the UoW:

```python
@dataclass(frozen=True)
class OrderPlaced(DomainEvent):
    order_id: str
    customer_id: str

class Order(UuidAggregate):
    def place(self, customer_id: str) -> None:
        self.raise_event(OrderPlaced(
            order_id=str(self.id.value),
            customer_id=customer_id,
        ))

# Publish flow:
with uow(order_repo):
    order = Order(...)
    order.place(customer_id="user-42")
    order_repo.save(order)   # repo collects OrderPlaced
    uow.commit()
    # → box.publish([OrderPlaced(...)]) → outbox_repo.save_many(events)
    # → outbox rows + order row committed in ONE transaction
```

If the process crashes after commit, the outbox relay will find the surviving rows on the next poll.

---

## Consumer — receiving events

On the consumer side, subscribe handlers to `box` before starting background tasks. The inbox deduplicates by event `id` — if the broker delivers the same event twice, the second delivery is silently skipped:

```python
class FulfillOrder(IEventHandler[OrderPlaced]):
    def handle(self, event: OrderPlaced) -> None:
        print(f"Fulfilling order {event.order_id}")

class NotifyWarehouse(IEventHandler[OrderPlaced]):
    def handle(self, event: OrderPlaced) -> None:
        print(f"Notifying warehouse for order {event.order_id}")

box.subscribe(FulfillOrder())
box.subscribe(NotifyWarehouse())
```

Handlers run synchronously when the inbox dispatches — the same ordering guarantees as `EventBus` apply within a single event delivery.

---

## Sequence (crash-safe)

```
Order service:
  order.place()              → OrderPlaced in order._events
  order_repo.save(order)     → repo collects OrderPlaced
  uow.commit():
    ├─ INSERT INTO orders
    └─ INSERT INTO outbox  ← same transaction; crash here = no data loss

  [crash here? outbox row survives]

Outbox relay (restarts after crash):
  SELECT * FROM outbox  → finds the surviving row
  broker.publish(...)   → sends to Kafka / RabbitMQ / Redis
  DELETE FROM outbox    → cleanup

Fulfillment service:
  for message in broker:
    is_processed(message.id) → False
    INSERT INTO inbox
    FulfillOrder.handle(event)   → runs
    NotifyWarehouse.handle(event) → runs
    UPDATE inbox SET processed = true
    broker.ack()

  [duplicate delivery? dispatch skipped — idempotent by inbox]
```

---

## Testing without a broker

Replace the broker with an in-memory implementation. The `RepositoryEventPublisher` and `RepositoryEventSubscriber` in the builder connect directly, bypassing the broker layer entirely:

```python
from hike.events.builder import TransactionalBoxBuilder
from hike.events.event_repository import RepositoryEventPublisher, RepositoryEventSubscriber
from hike.persistence.providers.in_memory import InMemoryDBContext, InMemoryRepository

outbox_context = InMemoryDBContext()
outbox_repo = InMemoryRepository()
outbox_uow = UnitOfWork(outbox_context)

inbox_context = InMemoryDBContext()
inbox_repo = InMemoryRepository()
inbox_uow = UnitOfWork(inbox_context)

# Wire publisher directly to subscriber — no external broker
box = (
    TransactionalBoxBuilder()
    .outbox_repository(outbox_repo, outbox_uow)
    .inbox_repository(inbox_repo, inbox_uow)
    .broker_publisher(RepositoryEventPublisher(inbox_repo))   # outbox → inbox directly
    .broker_subscriber(RepositoryEventSubscriber(inbox_repo, inbox_uow))
    .build()
)
```

---

## Quick reference

```python
from hike import UnitOfWork
from hike.events.builder import TransactionalBoxBuilder
from hike.events.interfaces import IEventHandler

# 1. Build
box = (
    TransactionalBoxBuilder()
    .outbox_repository(outbox_repo, outbox_uow)
    .inbox_repository(inbox_repo, inbox_uow)
    .with_kafka(producer=producer, consumer=consumer)  # or .with_rabbitmq() / .with_redis_broker()
    .build()
)

# 2. Subscribe handlers (inbox — receives)
box.subscribe(MyHandler())

# 3. Connect to UoW (outbox — produces)
uow = UnitOfWork(domain_context, event_producer=box)

# 4. Start background tasks
import threading
for task in box.tasks():
    threading.Thread(target=task, daemon=True).start()

# 5. Use normally — events go to outbox atomically on commit
with uow(repo):
    aggregate.do_something()
    repo.save(aggregate)
    uow.commit()
```

---

## Domain events vs. integration events

| | Domain events | Integration events |
| :--- | :--- | :--- |
| **Scope** | Within a bounded context | Across service boundaries |
| **Delivery** | In-memory, synchronous | Via broker (Kafka, RabbitMQ, Redis) |
| **Crash safety** | Not crash-safe — process crash = lost | Crash-safe via outbox |
| **Consistency** | Strong — handler failure = rollback | Eventual — inbox deduplication |
| **Hike API** | `EventBus` | `TransactionalBox` / `TransactionalBoxBuilder` |
