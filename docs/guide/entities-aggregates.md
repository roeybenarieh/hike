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
from hike import UuidEntity

class User(UuidEntity):
    name: str
    email: str
    # ↑ No need to declare `id` — UuidEntity generates a UUID property automatically.
```

---

## 2. What is an Aggregate?

An **Aggregate** is a cluster of associated Entities and Value Objects treated as a single unit for data changes. 

Think of an **Order**:
- An `Order` has an ID.
- An `Order` contains a list of `OrderItem`s (Value Objects or smaller entities).
- An `Order` also has a total price (`Price`).

The **Aggregate** is the main entry-point entity (in this case, `Order`). You never modify the order items directly from the outside; you always interact through the `Order` aggregate to ensure your business rules remain intact!

In Hike, you define an aggregate with `UuidAggregate`:

```python
from hike import UuidAggregate, UuidEntity, Field, ValueObject

class Name(ValueObject[str]): ...
class Price(ValueObject[float]): ...

class Engine(UuidEntity):
    name: Field[Name]

class Car(UuidAggregate):
    name: Field[Name]
    price: Field[Price]
    engine: Field[Engine]
    # ↑ Neither class declares `id` — it's generated automatically as a UUID.
```

---

## 3. 🔒 Aggregate Boundaries

Aggregates are not just a grouping convenience — they define strict boundaries around consistency, persistence, and ownership. Two rules govern how aggregates interact with each other and with a database.

### Reference other aggregates by ID only

No object inside an aggregate — not the aggregate, not an internal entity, not a value object — may hold a direct reference to any object that lives inside a *different* aggregate. The only permitted cross-aggregate reference is to other aggregate, and **only by its ID**.

```python
from uuid import UUID
from hike import UuidAggregate, UuidEntity, Field, ValueObject
from hike.entity import EntityID

# Two completely separate aggregates:

class Customer(UuidAggregate): ...

class WarehouseSlot(UuidEntity): ...    # an entity that lives *inside* Warehouse
class Warehouse(UuidAggregate):
    slot: Field[WarehouseSlot]


# ❌ Wrong — Order and OrderItem reach into other aggregates

class OrderItem(UuidEntity):
    slot: Field[WarehouseSlot]      # WarehouseSlot belongs to Warehouse, not to Order

class Order(UuidAggregate):
    customer: Field[Customer]       # direct reference to another aggregate instead of reference by ID
    items: list[OrderItem]


# ✅ Right — only store IDs of the other aggregate

class OrderItem(UuidEntity):
    warehouse_id: Field[EntityID[UUID]]    # store the Warehouse aggregate ID; load it separately

class Order(UuidAggregate):
    customer_id: Field[EntityID[UUID]]      # store the Customer aggregate ID; load it separately
    items: list[OrderItem]
```

This rule applies at every level of nesting. An `OrderItem` that lives inside `Order` is still bound by `Order`'s boundary — it may not reach into `Warehouse` to grab a `WarehouseSlot`. It may only store a `WarehouseID` (the aggregate ID).

Storing IDs keeps each aggregate independent — you fetch the related aggregate explicitly, only when you actually need it, through its own repository (more about repositories later in the docs).

### Load and save whole aggregates

When your application needs to work with an `Order`, it loads the *entire* `Order` — all its items, its total, its status, everything at once. There is no concept of fetching just the discount field and patching it in isolation. Likewise, when saving changes, the whole aggregate goes back to storage.

This guarantees that invariant checks always see the complete picture. A partial update could leave the aggregate in a state where its own rules are violated without any validation code ever running.

---

## 4. 🛡️ Protecting Business Rules (Invariants)

In business software, certain rules must **never** be broken — not about a single value, but about the **combined state** of an aggregate. For example:
- *A discount cannot exceed the order total.*
- *A meeting's end time must be after its start time.*

Hike enforces these with the `@rule` decorator and an `__invariants__` list:

```python
from hike import UuidAggregate, Field, ValueObject, rule, non_negative

class Price(ValueObject[float]):
    __validators__ = [non_negative]   # Price ensures its own value is valid

class Discount(ValueObject[float]):
    __validators__ = [non_negative]   # Discount ensures its own value is valid

@rule(message="Discount cannot exceed the order total")
def discount_within_total(order: "Order") -> bool:
    return order.discount.value > order.total.value   # True = rule is broken

class Order(UuidAggregate):
    total: Field[Price]
    discount: Field[Discount]
    __invariants__ = [discount_within_total]

Order(total=Price(100), discount=Discount(20))    # OK
Order(total=Price(100), discount=Discount(150))   # raises RuleBrokenError
```

!!! note "Who is responsible for what?"
    **Value Objects** validate their *own* data. Whether a `Price` is negative is `Price`'s job — it knows everything it needs from its single value.

    **Aggregates** validate their *combined state*. Whether a discount makes sense *relative to* a total is `Order`'s job, because it requires looking at two fields together.

    A useful rule of thumb: if the rule only needs one value to decide, it belongs in a `ValueObject`. If it needs two or more fields, it belongs in an aggregate invariant.

---

---

## Quick Reference

```python
from hike import UuidEntity, UuidAggregate, Field, ValueObject, rule, RuleBrokenError
from hike.entity import EntityID
from uuid import UUID

# Value Object (immutable, equality by value)
class Price(ValueObject[float]):
    value: float

# Entity (identity by id, mutable)
class User(UuidEntity):
    name: str        # plain scalar field — no Field[] needed

# Aggregate (cluster of entities + VOs, enforces invariants)
class Engine(UuidEntity):
    speed: Field[Speed]

class Car(UuidAggregate):
    engine: Field[Engine]   # nested entity — read-only outside @command

# Cross-aggregate reference: store IDs, not objects
class Order(UuidAggregate):
    customer_id: Field[EntityID[UUID]]   # ✅ ID reference
    # customer: Field[Customer]          # ❌ direct object reference

# Invariants — protect combined state
@rule(message="Discount cannot exceed the order total")
def discount_within_total(order: "Order") -> bool:
    return order.discount.value > order.total.value   # True = broken

class Order(UuidAggregate):
    total: Field[Price]
    discount: Field[Price]
    __invariants__ = [discount_within_total]
```

## Recommended External Reading

- [Martin Fowler's Aggregate Pattern Overview](https://martinfowler.com/bliki/DDD_Aggregate.html) — A great deep-dive into aggregates.
- **Next Step**: Learn how to enforce invariants on mutations using **[Rules, Commands & Invariants](rules-commands.md)**, then query your models with **[Specifications](specifications.md)**.
- For rules that span **multiple aggregates** and for publishing domain events to an `EventBus`, see **[Cross-Aggregate Invariants](cross-aggregate-invariants.md)**.