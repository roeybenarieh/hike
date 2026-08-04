"""Tests for the Specification pattern based on examples/specification.py."""

from __future__ import annotations

from typing import Any, cast

import pytest

from hike.ddd.entity import TerminalFieldProxy
from hike.ddd.value_object import ValueObject
from hike.ddd.specifications import (
    AndSpecification,
    BaseFilterSpecification,
    EqualSpecification,
    GreaterThanEqualSpecification,
    GreaterThanSpecification,
    ISpecification,
    ISpecificationVisitor,
    LessThanEqualSpecification,
    LessThanSpecification,
    NotEqualSpecification,
    NotSpecification,
    OrSpecification,
)
from tests.hike.ddd.conftest import Category, ListingName, Price, ProductListing, Rating


# ---------------------------------------------------------------------------
# In-memory filter visitor (mirrors examples/specification.py)
# ---------------------------------------------------------------------------


def _field_value(obj: object, proxy: TerminalFieldProxy) -> Any:
    val: Any = obj
    for name in proxy.path:
        val = getattr(val, name)
    return cast(ValueObject[Any], val).value if isinstance(val, ValueObject) else val


class ListingFilterSpecificationVisitor(ISpecificationVisitor):
    def __init__(self, listing: ProductListing) -> None:
        self._listing = listing
        self.result: bool = False

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

    def _actual(self, spec: BaseFilterSpecification) -> Any:
        return _field_value(self._listing, spec.field)

    def visit_equal(self, spec: EqualSpecification) -> None:
        self.result = self._actual(spec) == spec.operand

    def visit_not_equal(self, spec: NotEqualSpecification) -> None:
        self.result = self._actual(spec) != spec.operand

    def visit_greater_than(self, spec: GreaterThanSpecification) -> None:
        self.result = self._actual(spec) > spec.operand

    def visit_greater_than_equal(self, spec: GreaterThanEqualSpecification) -> None:
        self.result = self._actual(spec) >= spec.operand

    def visit_less_than(self, spec: LessThanSpecification) -> None:
        self.result = self._actual(spec) < spec.operand

    def visit_less_than_equal(self, spec: LessThanEqualSpecification) -> None:
        self.result = self._actual(spec) <= spec.operand


def _evaluate(listing: ProductListing, spec: ISpecification) -> bool:
    visitor = ListingFilterSpecificationVisitor(listing)
    spec.accept(visitor)
    return visitor.result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def budget_headphones() -> ProductListing:
    return ProductListing(
        price=Price(29.99),
        category=Category("electronics"),
        rating=Rating(3.8),
        name=ListingName("Budget Headphones"),
    )


@pytest.fixture
def pro_headphones() -> ProductListing:
    return ProductListing(
        price=Price(199.99),
        category=Category("electronics"),
        rating=Rating(4.7),
        name=ListingName("Pro Headphones"),
    )


@pytest.fixture
def python_book() -> ProductListing:
    return ProductListing(
        price=Price(45.00),
        category=Category("books"),
        rating=Rating(4.9),
        name=ListingName("Python Book"),
    )


@pytest.fixture
def desk_lamp() -> ProductListing:
    return ProductListing(
        price=Price(35.00),
        category=Category("home"),
        rating=Rating(4.2),
        name=ListingName("Desk Lamp"),
    )


@pytest.fixture
def cheap_cable() -> ProductListing:
    return ProductListing(
        price=Price(5.99),
        category=Category("electronics"),
        rating=Rating(2.1),
        name=ListingName("Cheap Cable"),
    )


# ---------------------------------------------------------------------------
# Tests: specification construction
# ---------------------------------------------------------------------------


class TestSpecificationConstruction:
    def test_equal_spec_from_field_proxy(self) -> None:
        spec = ProductListing.category == "electronics"
        assert isinstance(spec, EqualSpecification)
        assert spec.operand == "electronics"
        assert spec.field.field_name == "category"

    def test_not_equal_spec(self) -> None:
        spec = ProductListing.category != "electronics"
        assert isinstance(spec, NotEqualSpecification)

    def test_less_than_spec(self) -> None:
        spec = ProductListing.price < 50
        assert isinstance(spec, LessThanSpecification)
        assert spec.operand == 50

    def test_less_than_equal_spec(self) -> None:
        spec = ProductListing.price <= 50
        assert isinstance(spec, LessThanEqualSpecification)

    def test_greater_than_spec(self) -> None:
        spec = ProductListing.price > 50
        assert isinstance(spec, GreaterThanSpecification)

    def test_greater_than_equal_spec(self) -> None:
        spec = ProductListing.rating >= 4.0
        assert isinstance(spec, GreaterThanEqualSpecification)


class TestSpecificationComposition:
    def test_and_composition(self) -> None:
        spec = (ProductListing.price <= 50) & (ProductListing.category == "electronics")
        assert isinstance(spec, AndSpecification)
        assert isinstance(spec.left, LessThanEqualSpecification)
        assert isinstance(spec.right, EqualSpecification)

    def test_or_composition(self) -> None:
        spec = (ProductListing.price <= 50) | (ProductListing.rating >= 4.0)
        assert isinstance(spec, OrSpecification)

    def test_not_composition(self) -> None:
        spec = ~(ProductListing.category == "electronics")
        assert isinstance(spec, NotSpecification)
        assert isinstance(spec.spec, EqualSpecification)

    def test_complex_composition(self) -> None:
        affordable = ProductListing.price <= 50
        electronics = ProductListing.category == "electronics"
        well_rated = ProductListing.rating >= 4.0
        spec = (affordable & electronics) | well_rated
        assert isinstance(spec, OrSpecification)
        assert isinstance(spec.left, AndSpecification)


# ---------------------------------------------------------------------------
# Tests: visitor evaluation
# ---------------------------------------------------------------------------


class TestVisitorEvaluation:
    def test_equal_match(self, budget_headphones: ProductListing) -> None:
        spec = ProductListing.category == "electronics"
        assert _evaluate(budget_headphones, spec) is True

    def test_equal_no_match(self, python_book: ProductListing) -> None:
        spec = ProductListing.category == "electronics"
        assert _evaluate(python_book, spec) is False

    def test_not_equal_match(self, python_book: ProductListing) -> None:
        spec = ProductListing.category != "electronics"
        assert _evaluate(python_book, spec) is True

    def test_less_than_match(self, budget_headphones: ProductListing) -> None:
        spec = ProductListing.price < 50
        assert _evaluate(budget_headphones, spec) is True

    def test_less_than_no_match(self, pro_headphones: ProductListing) -> None:
        spec = ProductListing.price < 50
        assert _evaluate(pro_headphones, spec) is False

    def test_less_than_equal_boundary(self, python_book: ProductListing) -> None:
        spec = ProductListing.price <= 45.0
        assert _evaluate(python_book, spec) is True

    def test_greater_than_match(self, pro_headphones: ProductListing) -> None:
        spec = ProductListing.price > 100
        assert _evaluate(pro_headphones, spec) is True

    def test_greater_than_equal_boundary(self, pro_headphones: ProductListing) -> None:
        spec = ProductListing.rating >= 4.7
        assert _evaluate(pro_headphones, spec) is True

    def test_not_spec(self, python_book: ProductListing) -> None:
        spec = ~(ProductListing.category == "electronics")
        assert _evaluate(python_book, spec) is True

    def test_and_spec_both_true(self, budget_headphones: ProductListing) -> None:
        spec = (ProductListing.price <= 50) & (ProductListing.category == "electronics")
        assert _evaluate(budget_headphones, spec) is True

    def test_and_spec_one_false(self, python_book: ProductListing) -> None:
        spec = (ProductListing.price <= 50) & (ProductListing.category == "electronics")
        assert _evaluate(python_book, spec) is False

    def test_or_spec_either_true(self, pro_headphones: ProductListing) -> None:
        # pro_headphones: price=199.99 (not <= 50), rating=4.7 (>= 4.0) → True via right
        spec = (ProductListing.price <= 50) | (ProductListing.rating >= 4.0)
        assert _evaluate(pro_headphones, spec) is True

    def test_or_spec_both_false(self, cheap_cable: ProductListing) -> None:
        # cheap_cable: price=5.99, rating=2.1 — price <= 50 is True
        # Use a spec where both conditions fail
        spec = (ProductListing.price > 100) | (ProductListing.rating >= 4.0)
        assert _evaluate(cheap_cable, spec) is False


# ---------------------------------------------------------------------------
# Tests: end-to-end catalog filtering (mirrors the example's main block)
# ---------------------------------------------------------------------------


class TestCatalogFiltering:
    @pytest.fixture
    def all_listings(
        self,
        budget_headphones: ProductListing,
        pro_headphones: ProductListing,
        python_book: ProductListing,
        desk_lamp: ProductListing,
        cheap_cable: ProductListing,
    ) -> list[ProductListing]:
        return [budget_headphones, pro_headphones, python_book, desk_lamp, cheap_cable]

    def _filter(self, listings: list[ProductListing], spec: ISpecification) -> list[ProductListing]:
        return [l for l in listings if _evaluate(l, spec)]

    def test_affordable_electronics_or_well_rated(self, all_listings: list[ProductListing]) -> None:
        affordable = ProductListing.price <= 50
        electronics = ProductListing.category == "electronics"
        well_rated = ProductListing.rating >= 4.0
        spec = (affordable & electronics) | well_rated

        results = self._filter(all_listings, spec)
        names = {r.name.value for r in results}
        # budget_headphones: affordable(29.99<=50) + electronics → True
        # pro_headphones: not affordable(199.99), but well_rated(4.7>=4.0) → True
        # python_book: well_rated(4.9>=4.0) → True
        # desk_lamp: well_rated(4.2>=4.0) → True
        # cheap_cable: affordable(5.99<=50) + electronics → True (even though rating=2.1)
        assert names == {"Budget Headphones", "Pro Headphones", "Python Book", "Desk Lamp", "Cheap Cable"}

    def test_affordable_non_electronics(self, all_listings: list[ProductListing]) -> None:
        affordable = ProductListing.price <= 50
        electronics = ProductListing.category == "electronics"
        spec = affordable & ~electronics

        results = self._filter(all_listings, spec)
        names = {r.name.value for r in results}
        # python_book: 45.00, books → True
        # desk_lamp: 35.00, home → True
        assert names == {"Python Book", "Desk Lamp"}

    def test_expensive_items(self, all_listings: list[ProductListing]) -> None:
        spec = ProductListing.price > 100
        results = self._filter(all_listings, spec)
        assert len(results) == 1
        assert results[0].name.value == "Pro Headphones"

    def test_name_not_equal(self, all_listings: list[ProductListing]) -> None:
        spec = ProductListing.name != "Cheap Cable"
        results = self._filter(all_listings, spec)
        assert len(results) == 4
        assert all(r.name.value != "Cheap Cable" for r in results)
