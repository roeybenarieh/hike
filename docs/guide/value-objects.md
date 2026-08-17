# Value Objects

If you’ve never heard of a **Value Object**, don’t worry! It’s one of the simplest and most powerful ideas in Domain-Driven Design.

---

## What is a Value Object?

Imagine you have a $20 bill in your pocket. 
- If you swap that $20 bill for *another* $20 bill, do you care which one you have? 
- No! Because a $20 bill is defined entirely by its **value**. It doesn't have a serial number that you care about. If both bills have a value of 20, they are completely identical and interchangeable.

In software, **Value Objects** are things that represent a value rather than a unique identity. 

### Common Examples:
- **Money** (Amount: $10.00, Currency: USD)
- **Email Address** (Value: `user@example.com`)
- **Address** (Street, City, Postal Code)
- **Dimensions** (Width, Height)

---

## Why Use Value Objects?

Beginner developers often use raw strings, floats, and integers everywhere (this is called **Primitive Obsession**):

```python
# ❌ Bad: What if we pass negative price or invalid currency?
price = -50.0 
currency = "USDD" # Typo!
```

With Hike's **Value Objects**, you bundle the value and its validation rules together so they are *impossible* to make invalid:

```python
from hike import ValueObject, positive

class Price(ValueObject[float]):
    value: float
    __validators__ = [positive]

# ✅ Safe! This will immediately raise a ValueError if invalid.
my_price = Price(49.99)
```

---

## Key Benefits of Hike Value Objects

1. 🔒 **Frozen & Immutable**: Once you create a `Price(49.99)`, you cannot accidentally change its `.value` later. It's read-only.
2. ✅ **Auto-Validation**: Validators run automatically when created.
3. ⚖️ **Comparison Operators Built-In**: You can compare them naturally:
   ```python
   Price(10) < Price(20)  # Returns True!
   ```

---

---

## Comparison & Arithmetic Operators

Value Objects support comparison and arithmetic operators out of the box. Both sides of any operation may be another Value Object of the same type **or** the raw value directly.

### Comparison

```python
Price(10) < Price(20)    # True
Price(10) <= 10.0        # True  — raw value works too
Price(10) > Price(5)     # True
Price(10) >= Price(10)   # True
Price(10) == Price(10)   # True  — equality by value
```

Pyright enforces operand types — `Price(10) < "hello"` is a type error at edit time.

### ➕ Arithmetic

Arithmetic operators return a **new instance of the same concrete type** — `Price + Price` gives `Price`, not a raw number:

```python
a = Price(10.0)
b = Price(3.0)

a + b       # Price(13.0)
a - b       # Price(7.0)
a * 2       # Price(20.0)  — scalar on either side
4.0 / a     # Price(0.4)   — reflected forms work too
```

Because the result is the concrete type, **validators run on the new instance** ✅:

```python
class Price(ValueObject[float]):
    value: float
    __validators__ = [positive]

Price(5.0) - Price(10.0)   # raises ValueError — result -5.0 fails the positive check
```

---

## Built-in Validators Reference

### Numeric

| Validator | Usage | Description |
| :--- | :--- | :--- |
| `positive` | `[positive]` | Value must be strictly greater than zero |
| `non_negative` | `[non_negative]` | Value must be zero or greater |
| `min_value(n)` | `[min_value(0)]` | Value must be `>= n` |
| `max_value(n)` | `[max_value(100)]` | Value must be `<= n` |
| `between(a, b)` | `[between(0, 100)]` | Value must satisfy `a <= value <= b` |

### String

| Validator | Usage | Description |
| :--- | :--- | :--- |
| `non_empty` | `[non_empty]` | Must not be empty or whitespace-only |
| `min_length(n)` | `[min_length(3)]` | Length must be `>= n` |
| `max_length(n)` | `[max_length(255)]` | Length must be `<= n` |
| `matches(pattern)` | `[matches(r"\d{4}")]` | Must fully match the regex pattern |

### Datetime

All datetime validators accept both timezone-aware and naive `datetime` objects. Timezone-aware values are compared against UTC now; naive values against local now.

| Validator | Usage | Description |
| :--- | :--- | :--- |
| `in_past` | `[in_past]` | Value must be strictly before now |
| `in_future` | `[in_future]` | Value must be strictly after now |
| `not_before(dt)` | `[not_before(cutoff)]` | Value must be `>= dt` |
| `not_after(dt)` | `[not_after(deadline)]` | Value must be `<= dt` |
| `within_past(delta)` | `[within_past(timedelta(days=30))]` | Value must fall within `[now - delta, now]` |
| `within_future(delta)` | `[within_future(timedelta(hours=1))]` | Value must fall within `[now, now + delta]` |

```python
from datetime import timedelta, datetime, UTC
from hike import ValueObject, in_future, within_past, not_before, within_future

class AppointmentTime(ValueObject[datetime]):
    value: datetime
    __validators__ = [in_future]            # appointment must not be in the past

class MeasuredAt(ValueObject[datetime]):
    value: datetime
    __validators__ = [within_past(timedelta(hours=24))]  # must be a recent reading

class TrialExpiry(ValueObject[datetime]):
    value: datetime
    __validators__ = [within_future(timedelta(days=30))]  # at most 30 days from now
```

---

## Quick Reference

```python
from hike import (
    ValueObject,
    # Numeric
    positive, non_negative,
    min_value, max_value, between,
    # String
    non_empty, min_length, max_length, matches,
    # Datetime
    in_past, in_future, not_before, not_after, within_past, within_future,
)
from datetime import timedelta

class Price(ValueObject[float]):
    value: float
    __validators__ = [positive]          # validated at construction time

# Construction
p = Price(49.99)
p.value   # 49.99 — immutable; assignment raises FrozenInstanceError

# Comparison — other side may be ValueObject or raw value
Price(10) < Price(20)    # True
Price(10) >= 10.0        # True

# Arithmetic — result is the same concrete type; validators run on result
Price(10) + Price(5)     # Price(15.0)
Price(10) - 3.0          # Price(7.0)
Price(10) * 2            # Price(20.0)
Price(10) / 4            # Price(2.5)
```

---

## Want to Learn More?

- **External Reading**: Read Martin Fowler's classic explanation on [Value Objects](https://martinfowler.com/bliki/ValueObject.html).
- **Next Step**: Now that you know about values, let's look at things that *do* have unique identities: **[Entities & Aggregates](entities-aggregates.md)**.
