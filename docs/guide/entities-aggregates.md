# Entities & Aggregates

Now that you understand **Value Objects** (things defined by their values, like money or email addresses), let’s talk about things that have a **unique identity** that never changes: **Entities** and **Aggregates**.

---

## 1. What is an Entity?

An **Entity** is an object that has a distinct **identity** which persists over time, even if its properties change.

### The Analogy:
Think of a **User account**. 
- If a user changes their name from "Alice" to "Alicia" and updates their email address, **they are still the same user**. 
- Their identity (e.g., their User ID) remains the exact same. 

In Hike, you define an entity by giving it a unique ID:

```python
from hike.ddd.entity import Entity, EntityID
from uuid import UUID, uuid4

class UserId(EntityID[UUID]):
    pass

class User(Entity[UUID]):
    id: UserId
    name: str
    email: str
```

---

## 2. What is an Aggregate?

An **Aggregate** is a cluster of associated Entities and Value Objects treated as a single unit for data changes. 

Think of an **Order**:
- An `Order` has an ID.
- An `Order` contains a list of `OrderItem`s (Value Objects or smaller entities).
- An `Order` also has a total price (`Price`).

The **Aggregate Root** is the main entry-point entity (in this case, `Order`). You never modify the order items directly from the outside; you always interact through the `Order` root to ensure your business rules remain intact!

---

## 3. Protecting Business Rules (Invariants & Rules)

In business software, certain rules must **never** be broken. For example:
- *An order must always contain at least one item.*
- *An account balance can never drop below zero.*

In Domain-Driven Design, these unbreakable truths are called **Invariants**. Hike makes it super simple to declare them using rules:

```python
from hike.ddd.aggregate import Aggregate, rule, Invariant
from uuid import UUID, uuid4

# Define a reusable rule
has_items = rule(lambda order: len(order.items) > 0, "An order must have items!")

class Order(Aggregate[UUID]):
    items: list[str] = field(default_factory=list)
    
    # Declare the invariant — Hike will enforce this automatically!
    __invariants__ = [
        Invariant(has_items, "items")
    ]
```

---

## 4. Domain Events (Something Happened!)

Often, when something important happens in your domain, you want other parts of your app to know about it. Examples:
- *An `Order` was placed* -> Send a confirmation email.
- *A `Payment` failed* -> Notify the customer.

Hike lets you record **Domain Events** directly inside your Aggregates:

```python
from hike.ddd.domain_event import DomainEvent
from dataclasses import dataclass

@dataclass(frozen=True)
class OrderPlaced(DomainEvent):
    order_id: UUID

class Order(Aggregate[UUID]):
    def complete_checkout(self) -> None:
        # Do some work...
        
        # Record the event!
        self.record_event(OrderPlaced(order_id=self.id.value))
```

---

## Recommended External Reading

- [Martin Fowler's Aggregate Pattern Overview](https://martinfowler.com/bliki/DDD_Aggregate.html) — A great deep-dive into aggregates.
- **Next Step**: Learn how to query your models cleanly using **[Specifications](specifications.md)**.