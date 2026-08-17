# Repositories & Unit of Work

When working with databases, developers often mix database queries directly into their business logic. Domain-Driven Design separates these concerns entirely using **Repositories** and the **Unit of Work** pattern.

---

## 1. What is a Repository?

A **Repository** acts like an in-memory collection of your Aggregates (like a python list or dictionary), hiding all the messy SQL or MongoDB code behind a clean interface.

### Why use it?
- Your business code doesn't need to know *how* data is saved (Postgres, MongoDB, Redis, or simple memory).
- It makes unit-testing super easy because you can swap out the database repository for a fake in-memory one in seconds.

```python
from hike import IRepository
from typing import Any
from uuid import UUID

# IRepository[TId, TAggregate, TSession]
class OrderRepository(IRepository[UUID, Order, Any]):
    def save(self, aggregate: Order) -> UUID:
        ...
```

### 🔒 Optimistic Concurrency Control (Safe Updates)
Hike repositories automatically track aggregate versions. If two users try to edit the same order at the same time, Hike raises an `OptimisticLockError` to prevent one user's changes from silently overwriting the other's!

---

## 2. What is a Unit of Work (UoW)?

When you perform multiple operations (e.g., *create an order, deduct payment, update stock*), you want **all** of them to succeed, or **none** of them to happen. This is called a transaction.

The **Unit of Work** pattern manages this transaction boundary. You create a `UnitOfWork` once (with just the database context), then pass the repositories you want to use each time you open a transaction:

```python
from hike.persistence.uow import UnitOfWork

uow = UnitOfWork(context)   # created once, reused across transactions

with uow(repo):             # pass repos when opening the transaction
    repo.save(order)
    uow.commit()            # save all changes to database
```

If anything crashes inside the `with` block before `.commit()` is called, Hike automatically **rolls back** 🔄 the transaction so your database never ends up in a half-updated state.

### Multiple repositories in one transaction

Pass multiple repos to `uow(...)` to share a single transaction across them — all writes are atomic:

```python
with uow(order_repo, payment_repo):
    order_repo.save(order)
    payment_repo.save(payment)
    uow.commit()
```

### Auto-commit

Pass `auto_commit=True` to commit automatically when the `with` block exits normally (no exception):

```python
with uow(repo, auto_commit=True):
    repo.save(order)
# uow.commit() is called automatically on __exit__
```

## 3. The `IRepository` Interface and Persistence Providers

Hike defines `IRepository[TId, TAggregate, TSession]` as the standard contract every repository must satisfy. Your code depends only on this interface — the underlying storage technology is swapped by changing a single constructor call in your setup code.

| Provider | When to use |
| :--- | :--- |
| `InMemoryRepository` 🧪 | Tests and prototyping — no database required |
| `SQLAlchemyRepository` 🏛️ | Relational databases (PostgreSQL, SQLite, MySQL, …) |
| `PyMongoRepository` 🍃 | MongoDB |
| `RedisRepository` ⚡ | Redis |

Every provider implements the same methods (`save`, `get_one`, `update`, `delete`, `get_many`, `count`, `upsert`) so switching backends requires no changes to the code that uses the repository.

For step-by-step setup instructions for each backend, see **[Persistence Providers](persistence-providers.md)**.

---

## 4. 🔍 Querying Aggregates with `get_many`

`IRepository` includes a `get_many` method that returns multiple aggregates matching a **specification** (a composable filter — see [Specifications](specifications.md)). By default it returns a plain `list[TAggregate]`:

```python
with uow(repo):
    orders = repo.get_many(Order.total > 0.0)   # list[Order]
```

### Ordering

Pass an `ordering` argument to `get_many` method in order to sort results. Build each `OrderBy` with `asc()` or `desc()`. Pass a single value or a list for multi-field sorting:

```python
from hike import asc, desc

# Single field
with uow(repo):
    boats = repo.get_many(Boat.price >= 0.0, ordering=asc(Boat.price))

# Single field descending
with uow(repo):
    boats = repo.get_many(Boat.price >= 0.0, ordering=desc(Boat.price))

# Multi-field — priority is left-to-right
with uow(repo):
    boats = repo.get_many(
        Boat.price >= 0.0,
        ordering=[asc(Boat.category), desc(Boat.price)],
    )
```

Without `pagination`, `get_many` returns a plain `list` regardless of whether `ordering` is passed.

### Pagination

Pass a `pagination` argument to get a `Page[TAggregate]` back instead of a plain list. Hike supports three pagination styles:

```python
page.items        # list[TAggregate] — current page of results
page.total        # int | None — total matching records (None for cursor pagination)
page.has_next     # bool — True if more results follow
page.next_cursor  # str | None — token for the next cursor page
```

#### Offset pagination

Best for: classic "page 1, page 2 …" UIs where you know the exact row to start from.

```python
from hike import OffsetPagination

with uow(repo):
    page = repo.get_many(
        Boat.price >= 0.0,
        ordering=[asc(Boat.price)],
        pagination=OffsetPagination(offset=0, limit=10),
    )

print(page.items)    # first 10 boats sorted by price
print(page.total)    # total matching boats
print(page.has_next) # True if there are more boats beyond offset+limit
```

Move to the next page: `OffsetPagination(offset=10, limit=10)`.

#### Page-number pagination

Best for: page-number UIs ("give me page 3").

```python
from hike import PagePagination

with uow(repo):
    page = repo.get_many(
        Boat.price >= 0.0,
        ordering=[asc(Boat.price)],
        pagination=PagePagination(page=2, page_size=10),  # 1-indexed
    )
```

`PagePagination(page=2, page_size=10)` is equivalent to `OffsetPagination(offset=10, limit=10)`.

#### Cursor pagination (keyset)

Best for: infinite-scroll UIs and large datasets. Uses the values of the last returned row as a cursor instead of a row count, so it stays fast at any depth.

```python
from hike import CursorPagination

cursor: str | None = None   # start from the beginning

while True:
    with uow(repo):
        page = repo.get_many(
            Boat.price >= 0.0,
            ordering=[asc(Boat.price)],
            pagination=CursorPagination(limit=10, cursor=cursor),
        )

    process(page.items)

    if not page.has_next:
        break
    cursor = page.next_cursor
```

- `page.total` is always `None` — a full row count defeats the performance benefit.
- The cursor is an opaque token; do not construct or modify it manually.

#### Combining filter, ordering, and pagination

```python
with uow(repo):
    page = repo.get_many(
        Boat.price > 20.0,
        ordering=[asc(Boat.price)],
        pagination=OffsetPagination(offset=0, limit=5),
    )
```

---

## Quick Reference

```python
from hike import (
    IRepository,
    UnitOfWork, OptimisticLockError,
    asc, desc,
    OffsetPagination, PagePagination, CursorPagination,
)
from hike.persistence.providers.in_memory import InMemoryRepository, InMemoryDBContext

# IRepository[TId, TAggregate, TSession] — implement for a custom backend
class OrderRepository(IRepository[UUID, Order, Any]): ...

# In-memory setup (tests)
ctx  = InMemoryDBContext()
repo = InMemoryRepository()
uow  = UnitOfWork(ctx)

# Save
with uow(repo):
    repo.save(order)      # insert; raises AggregateAlreadyExistError if duplicate
    uow.commit()

# Load & update
with uow(repo):
    order = repo.get_one(order_id)
    order.do_something()
    repo.update(order)    # version-checked; raises OptimisticLockError on conflict
    uow.commit()

# Query with ordering + pagination
with uow(repo):
    page = repo.get_many(
        Order.total >= 0.0,
        ordering=[asc(Order.total), desc(Order.id)],
        pagination=OffsetPagination(offset=0, limit=10),
    )
    page.items      # list[Order]
    page.total      # int | None
    page.has_next   # bool

# Domain events — in-memory sync
with uow(repo, bus=bus):
    order.place()
    repo.save(order)
    uow.commit()    # handlers run before db.commit(); failure rolls back

# Domain events — crash-safe outbox
with uow(repo, outbox=outbox_repo):
    order.place()
    repo.save(order)
    uow.commit()    # order row + outbox row committed atomically
```

## Recommended External Reading

- [Martin Fowler on the Repository Pattern](https://martinfowler.com/eaaCatalog/repository.html)
- [Martin Fowler on Unit of Work](https://martinfowler.com/eaaCatalog/unitOfWork.html)

---

**Next Step**: Learn how to wire up the SQLAlchemy backend using **[SQLAlchemy Mappers](sqlalchemy-mappers.md)**.
