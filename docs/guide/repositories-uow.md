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

## Recommended External Reading

- [Martin Fowler on the Repository Pattern](https://martinfowler.com/eaaCatalog/repository.html)
- [Martin Fowler on Unit of Work](https://martinfowler.com/eaaCatalog/unitOfWork.html)

---

Congratulations! You now understand the core building blocks of **Hike** and Domain-Driven Design. You're ready to build robust, maintainable Python applications! 🚀
