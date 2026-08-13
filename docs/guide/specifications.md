# Specifications & Querying

If you've ever written complex `if` statements or messy SQL `WHERE` clauses scattered all over your codebase, the **Specification Pattern** is here to save the day.

---

## What is a Specification?

A **Specification** is simply a business rule or query filter packaged into its own standalone, reusable object. 

Instead of writing raw database queries everywhere, you write a specification once and use it anywhere (in memory, in databases, or in validation checks).

---

## Composing Specifications (Like Lego Bricks!)

Hike allows you to combine multiple specifications using standard Python operators:
- `&` (And)
- `|` (Or)
- `~` (Not)

### Example:
Imagine you want to find products that are active **and** cost less than $50:

```python
# Create criteria easily using class-level property proxies
is_active = Product.status == "active"
is_affordable = Product.price < 50.0

# Combine them with standard operators!
active_and_affordable = is_active & is_affordable
```

This reads like plain English, is 100% type-safe, and can be translated into SQL or MongoDB queries under the hood!

---

## Recommended External Reading

- [Martin Fowler's Specification Pattern Primer](https://martinfowler.com/aps8/refactoringSpecification.html)
- **Next Step**: Learn how to save and load your models using **[Repositories & Unit of Work](repositories-uow.md)**.
