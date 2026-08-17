# Rules, Commands & Invariants

Hike gives you a first-class way to express **business rules** that must never be broken — and to make sure mutations always go through a checked, controlled path.

---

## The Three Building Blocks

| Concept | What it does |
| :--- | :--- |
| `@rule` 📏 | Turns a predicate function into a reusable `Rule` object |
| `__invariants__` 🛡️ | List of rules checked automatically whenever an Entity or Aggregate is constructed |
| `@command` ⚡ | Marks a mutation method; unlocks nested entity writes and optionally re-checks rules after the mutation |

---

## 1. Defining Rules with `@rule`

A **Rule** is a predicate that returns `True` when the rule is **broken** (i.e. the condition is violated). Use the `@rule` decorator to turn any function into a `Rule` with zero boilerplate:

```python
from hike import rule

@rule
def discount_within_total(order: "Order") -> bool:
    return order.discount > order.total   # True means the rule IS broken
```

The function name is used as the human-readable message. Override it with an explicit message:

```python
@rule(message="Meeting must last at least 15 minutes")
def minimum_duration(meeting: "Meeting") -> bool:
    delta = meeting.end_time.value - meeting.start_time.value
    return delta.total_seconds() < 15 * 60
```

---

## 2. Enforcing Invariants at Construction

Attach rules to any Entity or Aggregate by listing them in `__invariants__`. Hike checks every rule in the list automatically after `__init__` completes — including in subclass constructors:

```python
from hike import UuidAggregate, Field, ValueObject, rule

class Price(ValueObject[float]): ...
class Discount(ValueObject[float]): ...

@rule(message="Discount cannot exceed the order total")
def discount_within_total(order: "Order") -> bool:
    return order.discount.value > order.total.value   # True = broken

class Order(UuidAggregate):
    total: Field[Price]
    discount: Field[Discount]
    __invariants__ = [discount_within_total]

Order(total=Price(100), discount=Discount(20))    # OK
Order(total=Price(100), discount=Discount(150))   # raises RuleBrokenError immediately
```

Invariants are collected from the **full class hierarchy** (MRO), so a subclass inherits its parent's constraints automatically.

---

## 3. Catching Rule Violations (`RuleBrokenError`)

When an invariant fires, Hike raises `RuleBrokenError`. The exception carries a reference to the broken rule:

```python
from hike import RuleBrokenError

try:
    Order(total=Price(100), discount=Discount(150))
except RuleBrokenError as e:
    print(e.broken_rule)   # FunctionalRule('Discount cannot exceed the order total')
```

---

## 4. Commands: Controlled Mutations (`@command`)

Direct field assignment on nested `Field[Entity]` values is blocked outside a `@command` (see [ReadOnlyView](#5-the-readonlyview-guard) below). All mutations must go through a method decorated with `@command`.

### Bare `@command` — grants mutable access

```python
from hike import UuidAggregate, UuidEntity, Field, command

class Engine(UuidEntity):
    speed: Field[Speed]

class Car(UuidAggregate):
    engine: Field[Engine]

    @command
    def tune(self, new_speed: Speed) -> None:
        self.engine.speed = new_speed   # OK — we're inside @command
```

### `@command(invariants=[...])` — check specific rules after the mutation

```python
from hike import ValueObject, UuidAggregate, Field, command, rule

class OrderStatus(ValueObject[str]):
    value: str

@rule(message="Order must have at least one item")
def order_has_items(order: "Order") -> bool:
    return len(order.items) == 0

class Order(UuidAggregate):
    items: list[OrderItem]
    status: Field[OrderStatus]

    @command(invariants=[order_has_items])
    def ship(self) -> None:
        self.status = OrderStatus("shipped")
```

Hike runs each listed rule *after* the method body completes. If any rule is broken, `RuleBrokenError` is raised.

### `@command(invariants='all')` — re-check every rule in `__invariants__`

```python
class Order(UuidAggregate):
    items: list[OrderItem]
    status: Field[OrderStatus]
    total: Field[Price]
    __invariants__ = [order_total_matches_items]

    @command(invariants='all')
    def apply_discount(self, percent: float) -> None:
        factor = 1 - percent / 100
        self.total = Money(self.total.value * factor)
        for item in self.items:
            item.price = Money(item.price.value * factor)
```

`'all'` collects every rule from `__invariants__` across the full MRO, so no rule is missed even if defined on a parent class.

---

## 5. 🚫 The `ReadOnlyView` Guard

Hike automatically wraps `Field[Entity]` fields in a **read-only proxy** whenever they are accessed outside a `@command`. Any attempt to write through the proxy raises `AttributeError`:

```python
car = Car(engine=Engine(speed=Speed(0)))

# Reading is fine anywhere:
print(car.engine.speed.value)   # 0

# Writing outside @command is blocked:
car.engine.speed = Speed(100)
# AttributeError: Cannot set 'speed' on a read-only view of Engine.
#   Mutations must go through the aggregate root's @command methods.

# Use the proper command instead:
car.tune(Speed(100))            # OK
```

> **Note**: `list[Entity]` fields are plain dataclass fields and are not yet protected by `ReadOnlyView`. Only `Field[Entity]` descriptor fields carry this protection.

---

## 6. Rules on Plain Entities

`__invariants__` and `@command` are not limited to aggregates — they work on any `Entity` subclass:

```python
@rule(message="Current gear cannot exceed the engine's maximum")
def gear_within_range(engine: "Engine") -> bool:
    return engine.current_gear.value > engine.max_gear.value

class Engine(UuidEntity):
    current_gear: Field[Gear]
    max_gear: Field[Gear]
    __invariants__ = [gear_within_range]

    @command(invariants=[gear_within_range])
    def shift_to(self, gear: Gear) -> None:
        self.current_gear = gear

Engine(current_gear=Gear(1), max_gear=Gear(6))   # OK
Engine(current_gear=Gear(7), max_gear=Gear(6))   # raises RuleBrokenError
```

---

## 7. Rules That Span Multiple Aggregates

`Rule[T]` operates on a **single** aggregate. When a business rule involves two or more aggregates, you need a different approach — see **[Cross-Aggregate Invariants](cross-aggregate-invariants.md)**.

---

## 8. Advanced: Subclassing `Rule` Directly

The `@rule` decorator is a shortcut for the most common case. For complex rules with their own state or helper methods, subclass `Rule[T]` directly:

```python
from hike import Rule

class TotalMatchesItems(Rule[Order]):
    """Total must equal the sum of all item prices (within €0.01 tolerance)."""

    def is_broken(self, obj: Order) -> bool:
        expected = sum(item.price.value for item in obj.items)
        return abs(obj.total.value - expected) > 0.01
```

Subclass rules can be used anywhere `@rule`-decorated rules can.

---

## Quick Reference

```python
from hike import rule, command, Rule, RuleBrokenError

# Define a rule on an entity/aggregate
@rule
def my_rule(obj: MyType) -> bool:
    return obj.value <= 0          # True = broken

# With a custom message
@rule(message="Value must be positive")
def my_rule(obj: MyType) -> bool:
    return obj.value <= 0

# Attach to an entity/aggregate (checked at init)
class MyEntity(UuidEntity):
    __invariants__ = [my_rule]

# Mark a mutation method
@command
def mutate(self, ...) -> None: ...

# Check specific rules after mutation
@command(invariants=[my_rule])
def mutate(self, ...) -> None: ...

# Check all __invariants__ after mutation
@command(invariants='all')
def mutate(self, ...) -> None: ...
```