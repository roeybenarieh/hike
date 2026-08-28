# Specifications

If you've ever written complex `if` statements or messy SQL `WHERE` clauses scattered all over your codebase, the **Specification Pattern** is here to save the day.

---

## What is a Specification?

A **Specification** is a business rule or query filter packaged into its own standalone, reusable object.

Instead of writing raw database queries everywhere, you write a specification once and use it anywhere — in memory, in databases, or as a business rule check.

---

## 1. Building Specifications

Specifications are built using **class-level field comparisons**. Hike's `Field` descriptor turns any class-level attribute access into a specification object instead of reading a value:

```python
from hike import UuidAggregate, Field, ValueObject

class Name(ValueObject[str]): ...
class Price(ValueObject[float]): ...
class Category(ValueObject[str]): ...

class Product(UuidAggregate):
    name: Field[Name]
    price: Field[Price]
    category: Field[Category]

# These produce specification objects, not booleans:
is_cheap       = Product.price < 50.0
is_electronics = Product.category == "electronics"
contains_pro = Product.name.matches(r".*pro.*", case_insensitive=True)

# Check a single object:
product = Product(name=Name("Widget Pro"), price=Price(29.99), category=Category("electronics"))
is_cheap.is_satisfied(product)   # True

# Filter a collection:
products = [...]
cheap_ones = list(is_cheap.filter(products))
```

All comparison operators are supported:

| Operator | Meaning |
| :--- | :--- |
| `==` | equal |
| `!=` | not equal |
| `<` | less than |
| `<=` | less than or equal |
| `>` | greater than |
| `>=` | greater than or equal |
| `.matches(pattern)` | field value matches a POSIX ERE pattern |

---

## Limitation: no field-to-field comparisons

Specifications only compare a **field against a constant value**.  
`Product.price < Product.discount` is **not** supported — the right-hand side must be a literal.

??? note "Why this isn't supported"
    SQL, MongoDB, and the in-memory provider all support field-to-field filtering natively and efficiently. Redis, however, has no native way to compare two fields on the same record — it would require fetching every record and evaluating the comparison in Python (a full scan).

    Hike enforces a **provider parity rule**: a feature must work identically across all providers, or it must not be available in any of them. Because Redis cannot support field-to-field filtering efficiently, this capability is withheld from every provider rather than silently degrading performance for Redis users or raising `NotImplementedError` at runtime.

---

## 2. Composing Specifications

Combine specifications using standard Python operators:

| Operator | Meaning                        |
|:---------|:-------------------------------|
| &        | both must be satisfied         |
| &#124;   | at least one must be satisfied |
| ~        | inverts the result             |

```python
is_cheap        = Product.price < 50.0
is_electronics  = Product.category == "electronics"
is_well_rated   = Product.rating >= 4.0

# Combine freely — these read like plain English:
affordable_electronics = is_cheap & is_electronics
great_value            = affordable_electronics | is_well_rated
not_electronics        = ~is_electronics
```

---

## 3. Querying with Repositories

Pass any specification directly to `repo.get_many()` and Hike translates it into the appropriate query for the underlying backend (SQL, MongoDB, Redis, or in-memory):

```python
with uow(repo):
    cheap_electronics = repo.get_many(affordable_electronics)
```

Add ordering and pagination the same way:

```python
from hike import asc, OffsetPagination

with uow(repo):
    page = repo.get_many(
        Product.price < 50.0,
        ordering=asc(Product.price),
        pagination=OffsetPagination(offset=0, limit=20),
    )
```

The same specification object works unchanged whether the repository is backed by SQLAlchemy, PyMongo, Redis, or an in-memory store — no rewriting required.

---

## 4. Using Specifications as Business Rules (`SpecificationRule`)

Specifications describe *what must be true* — and that makes them a natural fit for business rules. `SpecificationRule` bridges the two worlds: wrap any specification in a rule and use it wherever a `Rule` is accepted.

The rule is **broken** when the specification is *not* satisfied.

```python
from hike import SpecificationRule, UuidAggregate, Field, ValueObject, RuleBrokenError

class Price(ValueObject[float]): ...
class Stock(ValueObject[int]): ...
class Status(ValueObject[str]): ...

class Product(UuidAggregate):
    price: Field[Price]
    stock: Field[Stock]
    status: Field[Status]

# A product is listable only when it is priced, in stock, and not discontinued —
# an invariant spanning three fields at once:
listable = SpecificationRule(
    (Product.price > 0.0) &
    (Product.stock > 0) &
    (Product.status != "discontinued")
)

try:
    listable.raise_on_broken_rule(product)
except RuleBrokenError as e:
    print("Invariant violated:", e.broken_rule)
```

### Using `SpecificationRule` as an invariant

`SpecificationRule` is a `Rule`, so it plugs straight into `__invariants__` and `@command`:

```python
from hike import UuidAggregate, Field, ValueObject, SpecificationRule, command

class Price(ValueObject[float]): ...
class Stock(ValueObject[int]): ...
class Status(ValueObject[str]): ...

class Product(UuidAggregate):
    price: Field[Price]
    stock: Field[Stock]
    status: Field[Status]

    __invariants__ = [SpecificationRule(...)]

    @command(invariants=[SpecificationRule(...)])
    def restock(self, quantity: Stock) -> None:
        self.stock = quantity
```

Use `SpecificationRule` when the constraint can be expressed as field comparisons — you get free repository querying and operator composition for nothing. Reach for `@rule` when the check requires custom Python logic (e.g. summing a list, calling a helper method).

---

## Quick Reference

```python
from hike import SpecificationRule, UuidAggregate, Field, ValueObject

# Build specs from field comparisons
is_active  = Product.status == "active"
is_cheap   = Product.price < 50.0
has_stock  = Product.quantity > 0

# Compose with operators
spec = is_active & (is_cheap | has_stock)
spec = ~is_active

# --- Regex (POSIX ERE) ---

# Basic pattern — validated at construction, not at query time
is_digital = Product.sku.matches(r"^DIG-[[:digit:]]{4}$")

# Case-insensitive
contains_pro = Product.name.matches(r"pro", case_insensitive=True)

# POSIX named character classes
has_digit   = Product.sku.matches(r"[[:digit:]]")
alpha_only  = Product.name.matches(r"^[[:alpha:]]+$")

# These raise re2.error immediately (PCRE constructs are rejected):
# Product.name.matches(r"\d+")        ← use [[:digit:]]+ instead
# Product.name.matches(r"(?=sale)")   ← lookaheads not supported

# Compose regex specs just like any other spec
spec = is_digital & (is_cheap | contains_pro)

# --- Query a repository ---
with uow(repo):
    results = repo.get_many(is_active & is_cheap)
    regex_results = repo.get_many(is_digital & is_active)

# Use as an invariant — combines multiple fields into one coherent rule
listable = SpecificationRule(
    (Product.price > 0.0) &
    (Product.stock > 0) &
    (Product.status != "discontinued")
)
listable.raise_on_broken_rule(product)   # raises RuleBrokenError if violated
listable.is_broken(product)              # bool

# Attach to the aggregate so it's enforced automatically
class Product(UuidAggregate):
    ...
    __invariants__ = [listable]
```

---

## Recommended External Reading

- [Martin Fowler's Specification Pattern](https://martinfowler.com/apsupp/spec.pdf)