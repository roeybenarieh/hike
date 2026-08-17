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

### Optimistic Concurrency Control (Safe Updates)
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

If anything crashes inside the `with` block before `.commit()` is called, Hike automatically **rolls back** the transaction so your database never ends up in a half-updated state.

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

## 3. `InMemoryRepository` — Testing Without a Database

Every Hike repository backend (SQLAlchemy, PyMongo, Redis) implements the same `IRepository` interface. For tests you can swap them all out with the built-in in-memory implementation:

```python
from hike.persistence.providers.in_memory import InMemoryRepository, InMemoryDBContext
from hike.persistence.uow import UnitOfWork

ctx = InMemoryDBContext()
repo = InMemoryRepository()
uow = UnitOfWork(ctx)

with uow(repo):
    repo.save(Order(price=Price(99)))
    uow.commit()

with uow(repo):
    order = repo.get_one(order_id)
    order.complete_checkout()
    repo.update(order)
    uow.commit()
```

The in-memory repository stores deep copies so each transaction sees an isolated snapshot. Optimistic locking works the same way as with real database backends — `OptimisticLockError` is raised on a version mismatch.

---

## 4. Ordering Results

Pass an `ordering` argument to `get_many` to sort the results. Each `OrderBy` is built from a field proxy using `.asc()` or `.desc()`.

You can pass **a single `OrderBy`** directly, or **a list** when sorting by multiple fields:

```python
# Single field — pass directly (no list needed)
with uow(repo):
    boats = repo.get_many(Boat.price >= 0.0, ordering=asc(Boat.price))

# Single field descending
with uow(repo):
    boats = repo.get_many(Boat.price >= 0.0, ordering=desc(Boat.price))

# Multi-field — pass a list; priority is left-to-right
with uow(repo):
    boats = repo.get_many(
        Boat.price >= 0.0,
        ordering=[asc(Boat.category), desc(Boat.price)],
    )
```

When `ordering` is provided **without** `pagination`, `get_many` still returns a plain `list` — fully backward-compatible.

---

## 5. Pagination

Hike supports three styles of pagination, each suited to a different use case. When you pass a `pagination` argument, `get_many` returns a `Page[T]` object instead of a plain list.

### `Page` — the result wrapper

```python
from hike.ddd import Page

page.items      # list[TAggregate] — the current page of results
page.total      # int | None — total matching records (None for cursor pagination)
page.has_next   # bool — True if there are more results after this page
page.next_cursor  # str | None — opaque token for the next cursor page
```

---

### Offset pagination

Best for: classic "page 1, page 2 …" UIs where you know the exact position to skip to.

```python
from hike.ddd import OffsetPagination

with uow(repo):
    page = repo.get_many(
        Boat.price >= 0.0,
        ordering=[asc(Boat.price)],
        pagination=OffsetPagination(offset=0, limit=10),
    )

print(page.items)    # first 10 boats sorted by price
print(page.total)    # total number of matching boats
print(page.has_next) # True if there are more boats beyond offset+limit
```

Move to the next page by incrementing `offset` by `limit`:

```python
next_page = OffsetPagination(offset=10, limit=10)
```

---

### Page-number pagination

Best for: situations where you think in terms of page numbers (e.g., "give me page 3").

```python
from hike.ddd import PagePagination

with uow(repo):
    page = repo.get_many(
        Boat.price >= 0.0,
        ordering=[asc(Boat.price)],
        pagination=PagePagination(page=2, page_size=10),  # page is 1-indexed
    )

print(page.items)    # boats 11–20 sorted by price
print(page.total)    # total matching boats
print(page.has_next) # True if page 3 exists
```

`PagePagination` is a convenience wrapper — `page=2, page_size=10` is equivalent to `OffsetPagination(offset=10, limit=10)`.

---

### Cursor pagination (keyset pagination)

Best for: infinite-scroll UIs, large datasets, or any case where offset pagination gets slow. Cursor pagination stays fast at any depth because it uses a **keyset** (the values of the last item) instead of a row count to find the next page.

```python
from hike.ddd import CursorPagination

cursor: str | None = None  # start from the beginning

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
    cursor = page.next_cursor  # pass the token to the next request
```

Key properties of cursor pagination:

- `page.total` is always `None` — counting all rows defeats the performance benefit.
- `page.next_cursor` is `None` when `has_next` is `False` (last page).
- The cursor is an opaque, base64-encoded token. Do not construct or modify it manually.
- Results are **stable** even when ordering fields have duplicate values — Hike automatically uses the aggregate `id` as an implicit tiebreaker.

---

### Combining specs, ordering, and pagination

All three arguments can be combined freely:

```python
with uow(repo):
    page = repo.get_many(
        Boat.price > 20.0,                        # filter
        ordering=[asc(Boat.price)],              # sort
        pagination=OffsetPagination(offset=0, limit=5),  # page
    )
```

---

### Backward compatibility

Calling `get_many` without a `pagination` argument always returns a plain `list`, so existing code needs no changes:

```python
# Still returns list[Boat]
with uow(repo):
    boats = repo.get_many(Boat.price > 0.0)
```

---

---

## Quick Reference

```python
from hike import UnitOfWork, OptimisticLockError, asc, desc
from hike import OffsetPagination, PagePagination, CursorPagination
from hike.persistence.providers.in_memory import InMemoryRepository, InMemoryDBContext

# Setup (in-memory — ideal for tests)
ctx  = InMemoryDBContext()
repo = InMemoryRepository()
uow  = UnitOfWork(ctx)

# Save
with uow(repo):
    repo.save(order)          # insert; raises AggregateAlreadyExistError if duplicate
    uow.commit()

# Load & update
with uow(repo):
    order = repo.get_one(order_id)
    order.do_something()
    repo.update(order)        # version-checked; raises OptimisticLockError on conflict
    uow.commit()

# Upsert (no version check)
with uow(repo):
    repo.upsert(order)
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
