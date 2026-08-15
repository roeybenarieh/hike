# Rules, Commands & Invariants

Hike gives you a first-class way to express **business rules** that must never be broken — and to make sure mutations always go through a checked, controlled path.

---

## The Three Building Blocks

| Concept | What it does |
| :--- | :--- |
| `@rule` | Turns a predicate function into a reusable `Rule` object |
| `__invariants__` | List of rules checked automatically whenever an Entity or Aggregate is constructed |
| `@command` | Marks a mutation method; unlocks nested entity writes and optionally re-checks rules after the mutation |

---

## 1. Defining Rules with `@rule`

A **Rule** is a predicate that returns `True` when the rule is **broken** (i.e. the condition is violated). Use the `@rule` decorator to turn any function into a `Rule` with zero boilerplate:

```python
from hike import rule

@rule
def price_positive(boat: "Boat") -> bool:
    return boat.price.value <= 0          # True means the rule IS broken
```

The function name is used as the human-readable message. Override it with an explicit message:

```python
@rule(message="Price must be between €1,000 and €500,000")
def price_in_range(boat: "Boat") -> bool:
    return not (1_000 <= boat.price.value <= 500_000)
```

Rules can be shared across types using a `Protocol`:

```python
from typing import Protocol

class HasValue(Protocol):
    value: int | float

@rule(message="Value must be positive")
def positive(obj: HasValue) -> bool:
    return obj.value <= 0
```

---

## 2. Enforcing Invariants at Construction (`__invariants__`)

Attach rules to any Entity or Aggregate by listing them in `__invariants__`. Hike checks every rule in the list automatically after `__init__` completes — including in subclass constructors:

```python
from hike import UuidAggregate, Field, ValueObject, rule

class Price(ValueObject[float]): ...

@rule(message="Price must be positive")
def price_positive(order: "Order") -> bool:
    return order.price.value <= 0

class Order(UuidAggregate):
    price: Field[Price]
    __invariants__ = [price_positive]

Order(price=Price(100))   # OK
Order(price=Price(-1))    # raises RuleBrokenError immediately
```

Invariants are collected from the **full class hierarchy** (MRO), so a subclass inherits its parent's constraints automatically.

---

## 3. Catching Rule Violations (`RuleBrokenError`)

When an invariant fires, Hike raises `RuleBrokenError`. The exception carries a reference to the broken rule:

```python
from hike import RuleBrokenError

try:
    Order(price=Price(-1))
except RuleBrokenError as e:
    print(e.broken_rule)   # FunctionalRule('Price must be positive')
```

---

## 4. Commands: Controlled Mutations (`@command`)

Direct field assignment on nested `Field[Entity]` values is blocked outside a `@command` (see [ReadOnlyView](#5-the-readonlyview-guard) below). All mutations must go through a method decorated with `@command`.

### Bare `@command` — just grants mutable access, no rule check

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
@rule(message="Order must have at least one item")
def order_has_items(order: "Order") -> bool:
    return len(order.items) == 0

class Order(UuidAggregate):
    items: list[OrderItem]

    @command(invariants=[order_has_items])
    def ship(self) -> None:
        self._shipped = True
```

Hike runs each listed rule *after* the method body completes. If any rule is broken, `RuleBrokenError` is raised and the state change is **not** rolled back (commands are not transactional by themselves — use a Unit of Work for that).

### `@command(invariants='all')` — re-check every rule in `__invariants__`

```python
class Order(UuidAggregate):
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

## 5. The `ReadOnlyView` Guard

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
@rule(message="Speed cannot be negative")
def speed_non_negative(engine: "Engine") -> bool:
    return engine.speed.value < 0

class Engine(UuidEntity):
    __invariants__ = [speed_non_negative]
    speed: Field[Speed]

    @command(invariants=[speed_non_negative])
    def set_speed(self, new_speed: Speed) -> None:
        self.speed = new_speed

Engine(speed=Speed(100))    # OK
Engine(speed=Speed(-1))     # raises RuleBrokenError
```

---

## 7. Rules That Span Multiple Aggregates

`Rule[T]` operates on a **single** aggregate. When a business rule involves two or more aggregates (e.g., "no duplicate email across all users", "a team cannot exceed its roster size"), use `CrossAggregateRule[T]` and a `DomainService` instead.

```python
from dataclasses import dataclass
from hike import CrossAggregateRule, RuleBrokenError

@dataclass
class UniqueEmailContext:
    email: str
    existing_count: int

class UniqueEmailRule(CrossAggregateRule[UniqueEmailContext]):
    def is_broken(self, context: UniqueEmailContext) -> bool:
        return context.existing_count > 0
```

Cross-aggregate rules are checked **explicitly** (they cannot be listed in `__invariants__`). See the full guide: **[Cross-Aggregate Invariants](cross-aggregate-invariants.md)**.

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

# Define a rule
@rule
def my_rule(obj: MyType) -> bool:
    return obj.value <= 0          # True = broken

# With a custom message
@rule(message="Value must be positive")
def my_rule(obj: MyType) -> bool:
    return obj.value <= 0

# Attach to a class (checked at init)
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

**Next Step**: Learn how to persist your aggregates using **[Repositories & Unit of Work](repositories-uow.md)**, or jump to **[Cross-Aggregate Invariants](cross-aggregate-invariants.md)** for rules that span multiple aggregates.
