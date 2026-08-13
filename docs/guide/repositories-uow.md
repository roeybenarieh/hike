# Repositories & Unit of Work

When working with databases, developers often mix database queries directly into their business logic. Domain-Driven Design separates these concerns entirely using **Repositories** and the **Unit of Work** pattern.

---

## 1. What is a Repository?

A **Repository** acts like an in-memory collection of your Aggregates (like a python list or dictionary), hiding all the messy SQL or MongoDB code behind a clean interface.

### Why use it?
- Your business code doesn't need to know *how* data is saved (Postgres, MongoDB, Redis, or simple memory).
- It makes unit-testing super easy because you can swap out the database repository for a fake in-memory one in seconds.

```python
from hike.ddd.repository import IRepository

# A clean, abstract repository interface for orders
class OrderRepository(IRepository[UUID, Any, Order]):
    def save(self, aggregate: Order) -> UUID:
        ...
```

### Optimistic Concurrency Control (Safe Updates)
Hike repositories automatically track aggregate versions. If two users try to edit the same order at the same time, Hike raises an `OptimisticLockError` to prevent one user's changes from silently overwriting the other's!

---

## 2. What is a Unit of Work (UoW)?

When you perform multiple operations (e.g., *create an order, deduct payment, update stock*), you want **all** of them to succeed, or **none** of them to happen. This is called a transaction.

The **Unit of Work** pattern manages this transaction boundary across multiple repositories:

```python
from hike.ddd.uow import UnitOfWork

# Wrap your transactional boundary in a `with` statement
with uow:
    order = uow.orders.get(order_id)
    order.status = "shipped"
    
    # Commit saves all changes atomically!
    uow.commit()
```
If anything crashes inside the `with` block before `.commit()` is called, Hike automatically **rolls back** the transaction so your database never ends up in a half-updated state.

---

---

## 3. `upsert()` — Insert or Update Without a Version Check

`update()` enforces optimistic locking and raises `OptimisticLockError` if the stored version has advanced. Sometimes you simply want to **write the latest state** regardless of version — for example, syncing a read model or applying an idempotent import.

Use `upsert()` for that:

```python
with uow:
    uow.repo.upsert(boat)   # inserts if not exists, overwrites if it does
    uow.commit()
```

`upsert()` does not raise `OptimisticLockError` and does not sync the version back onto the aggregate. If you need to keep writing to the aggregate after an upsert, re-fetch it with `get_one()` first.

---

## 4. `InMemoryRepository` — Testing Without a Database

Every Hike repository backend (SQLAlchemy, PyMongo, Redis) implements the same `IRepository` interface. For tests you can swap them all out with the built-in in-memory implementation:

```python
from hike.ddd.providers.in_memory.repository import InMemoryRepository
from hike.ddd.providers.in_memory.db_context import InMemoryDBContext
from hike.ddd.uow import UnitOfWork

ctx = InMemoryDBContext()
repo = InMemoryRepository()
uow = UnitOfWork(ctx, repo=repo)

with uow:
    uow.repo.save(Order(price=Price(99)))
    uow.commit()

with uow:
    order = uow.repo.get_one(order_id)
    order.complete_checkout()
    uow.repo.update(order)
    uow.commit()
```

The in-memory repository stores deep copies so each transaction sees an isolated snapshot. Optimistic locking works the same way as with real database backends — `OptimisticLockError` is raised on a version mismatch.

---

## Recommended External Reading

- [Martin Fowler on the Repository Pattern](https://martinfowler.com/eaaCatalog/repository.html)
- [Martin Fowler on Unit of Work](https://martinfowler.com/eaaCatalog/unitOfWork.html)

---

**Next Step**: Learn how to wire up the SQLAlchemy backend using **[SQLAlchemy Mappers](sqlalchemy-mappers.md)**.
