# Value Objects

If you’ve never heard of a **Value Object**, don’t worry! It’s one of the simplest and most powerful ideas in Domain-Driven Design.

---

## What is a Value Object? (The Analogy)

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

## Why Use Value Objects? (No More "Primitive Obsession")

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

1. **Frozen & Immutable**: Once you create a `Price(49.99)`, you cannot accidentally change its `.value` later. It's read-only.
2. **Auto-Validation**: Validators run automatically when created.
3. **Comparison Operators Built-In**: You can compare them naturally:
   ```python
   Price(10) < Price(20)  # Returns True!
   ```

---

## Want to Learn More?

- **External Reading**: Read Martin Fowler's classic explanation on [Value Objects](https://martinfowler.com/bliki/ValueObject.html).
- **Next Step**: Now that you know about values, let's look at things that *do* have unique identities: **[Entities & Aggregates](entities-aggregates.md)**.
