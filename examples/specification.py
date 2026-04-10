"""
Specification pattern with ValueObjects — cliff DDD example.

Accessing an Entity field at the *class* level (e.g. ``ProductListing.price``)
returns a ``FieldProxy``.  Comparing a ``FieldProxy`` against a value returns
a Specification object instead of a bool, which can then be composed with
& (and), | (or), and ~ (not):

    ProductListing.price > 50               →  GreaterThanSpecification(FieldProxy('price', ...), 50)
    ProductListing.category == "books"      →  EqualSpecification(FieldProxy('category', ...), "books")
    (ProductListing.price > 50)
        & (ProductListing.rating >= 4.0)    →  AndSpecification(...)

The aggregate owns a collection of entities.  Specifications are built
directly from the entity class's VO-typed fields and passed to the
aggregate, which applies them via the visitor pattern.
"""

from __future__ import annotations

from cliff.ddd.entity import Field, FieldProxy, UuidEntity
from cliff.ddd.specifications import (
    AndSpecification,
    EqualSpecification,
    GreaterThanEqualSpecification,
    GreaterThanSpecification,
    ISpecification,
    IVisitor,
    LessThanEqualSpecification,
    LessThanSpecification,
    NotEqualSpecification,
    NotSpecification,
    OrSpecification,
)
from cliff.ddd.value_object import ValueObject


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


class Price(ValueObject[float]):
    value: float

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("Price cannot be negative")


class Category(ValueObject[str]):
    value: str


class Rating(ValueObject[float]):
    """Star rating in the range [0.0, 5.0]."""

    value: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.value <= 5.0):
            raise ValueError("Rating must be between 0 and 5")


class ListingName(ValueObject[str]):
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("Name cannot be empty")


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------


class ProductListing(UuidEntity):
    """Entity representing a single product listing inside a catalog."""

    price: Field[Price]
    category: Field[Category]
    rating: Field[Rating]
    name: Field[ListingName]

    def __repr__(self) -> str:
        return (
            f"ProductListing({self.name.value!r},"
            f" price={self.price.value},"
            f" category={self.category.value!r},"
            f" rating={self.rating.value})"
        )


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


class ProductCatalog(UuidEntity):
    """Aggregate root that owns a collection of ProductListing entities."""

    listings: list[ProductListing]

    def filter_listings(self, spec: ISpecification) -> list[ProductListing]:
        """Return the listings that satisfy *spec*, evaluated via the visitor."""
        result: list[ProductListing] = []
        for listing in self.listings:
            visitor = ListingFilterVisitor(listing)
            spec.accept(visitor)
            if visitor.result:
                result.append(listing)
        return result


# ---------------------------------------------------------------------------
# In-memory filter visitor
#
# In a real application a DB-specific visitor (e.g. SQLAlchemySpecVisitor)
# would translate the spec tree into a query instead of evaluating it.
# ---------------------------------------------------------------------------


def _field_value(listing: ProductListing, proxy: FieldProxy) -> object:
    """Return the raw value of the field named by *proxy* on *listing*."""
    field_val = getattr(listing, proxy.field_name)
    return field_val.value if isinstance(field_val, ValueObject) else field_val  # type: ignore[union-attr]


class ListingFilterVisitor(IVisitor):
    """Evaluates a specification tree against a single ProductListing.

    After calling spec.accept(visitor), read visitor.result for the outcome.
    """

    def __init__(self, listing: ProductListing) -> None:
        self._listing = listing
        self.result: bool = False

    # --- composites ---

    def visit_and(self, spec: AndSpecification) -> None:
        spec.left.accept(self)
        left = self.result
        spec.right.accept(self)
        self.result = left and self.result

    def visit_or(self, spec: OrSpecification) -> None:
        spec.left.accept(self)
        left = self.result
        spec.right.accept(self)
        self.result = left or self.result

    def visit_not(self, spec: NotSpecification) -> None:
        spec.spec.accept(self)
        self.result = not self.result

    # --- filter leaves ---

    def _actual(self, spec: EqualSpecification) -> object:  # type: ignore[override]
        return _field_value(self._listing, spec.field)  # type: ignore[arg-type]

    def visit_equal(self, spec: EqualSpecification) -> None:
        self.result = self._actual(spec) == spec.operand  # type: ignore[attr-defined]

    def visit_not_equal(self, spec: NotEqualSpecification) -> None:
        self.result = self._actual(spec) != spec.operand  # type: ignore[attr-defined]

    def visit_greater_than(self, spec: GreaterThanSpecification) -> None:
        self.result = self._actual(spec) > spec.operand  # type: ignore[attr-defined]

    def visit_greater_than_equal(self, spec: GreaterThanEqualSpecification) -> None:
        self.result = self._actual(spec) >= spec.operand  # type: ignore[attr-defined]

    def visit_less_than(self, spec: LessThanSpecification) -> None:
        self.result = self._actual(spec) < spec.operand  # type: ignore[attr-defined]

    def visit_less_than_equal(self, spec: LessThanEqualSpecification) -> None:
        self.result = self._actual(spec) <= spec.operand  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    catalog = ProductCatalog(
        listings=[
            ProductListing(price=Price(29.99), category=Category("electronics"), rating=Rating(3.8), name=ListingName("Budget Headphones")),
            ProductListing(price=Price(199.99), category=Category("electronics"), rating=Rating(4.7), name=ListingName("Pro Headphones")),
            ProductListing(price=Price(45.00), category=Category("books"), rating=Rating(4.9), name=ListingName("Python Book")),
            ProductListing(price=Price(35.00), category=Category("home"), rating=Rating(4.2), name=ListingName("Desk Lamp")),
            ProductListing(price=Price(5.99), category=Category("electronics"), rating=Rating(2.1), name=ListingName("Cheap Cable")),
        ],
    )

    affordable: ISpecification = ProductListing.price <= 50
    electronics: ISpecification = ProductListing.category == "electronics"
    well_rated: ISpecification = ProductListing.rating >= 4.0

    # Compose with &, |, ~  then hand to the aggregate to filter its listings.
    value_pick: ISpecification = (affordable & electronics) | well_rated
    non_tech_deal: ISpecification = affordable & ~electronics

    print("=== Affordable electronics OR well-rated ===")
    for listing in catalog.filter_listings(value_pick):
        print(f"  {listing.name.value:25s}  ${listing.price.value:>7.2f}  ★{listing.rating.value}")

    print("\n=== Affordable non-electronics ===")
    for listing in catalog.filter_listings(non_tech_deal):
        print(f"  {listing.name.value:25s}  ${listing.price.value:>7.2f}  ★{listing.rating.value}")
