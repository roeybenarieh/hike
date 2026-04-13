"""Tests for ValueObject based on examples/value_objects.py."""

import pytest

from hike.ddd.value_object import ValueObject


class Price(ValueObject[float]):
    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("Price cannot be negative")


class Name(ValueObject[str]):
    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("Name cannot be empty")


class TestValueObjectValidation:
    def test_negative_price_raises(self) -> None:
        with pytest.raises(ValueError, match="Price cannot be negative"):
            Price(-1)

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError, match="Name cannot be empty"):
            Name("")

    def test_valid_price_created(self) -> None:
        p = Price(5.0)
        assert p.value == 5.0

    def test_valid_name_created(self) -> None:
        n = Name("Titanic")
        assert n.value == "Titanic"


class TestValueObjectEquality:
    def test_equal_values_are_equal(self) -> None:
        assert Price(5) == Price(5)

    def test_different_values_are_not_equal(self) -> None:
        assert Price(5) != Price(6)

    def test_same_value_different_instances_equal(self) -> None:
        p1 = Price(5)
        p3 = Price(5)
        assert p1 == p3

    def test_different_type_not_equal(self) -> None:
        # Price and Name with same raw value should not compare equal
        assert Price(5) != Name("5")


class TestValueObjectComparisons:
    def test_less_than(self) -> None:
        assert Price(5) < Price(6)

    def test_less_than_or_equal(self) -> None:
        assert Price(5) <= Price(5)
        assert Price(5) <= Price(6)

    def test_greater_than(self) -> None:
        assert Price(6) > Price(5)

    def test_greater_than_or_equal(self) -> None:
        assert Price(5) >= Price(5)
        assert Price(6) >= Price(5)

    def test_chained_comparison(self) -> None:
        p1 = Price(5)
        p2 = Price(6)
        p3 = Price(5)
        assert p1 <= p3 < p2

    def test_compare_with_raw_value(self) -> None:
        assert Price(5) < 10.0
        assert Price(10) > 5.0

    def test_cross_type_comparison_raises(self) -> None:
        with pytest.raises(TypeError):
            Price(5) < Name("something")  # pyright: ignore[reportOperatorIssue, reportUnusedExpression]


class TestValueObjectImmutability:
    def test_frozen_raises_on_set(self) -> None:
        p = Price(5)
        with pytest.raises((AttributeError, TypeError)):
            p.value = 10  # pyright: ignore[reportAttributeAccessIssue]
