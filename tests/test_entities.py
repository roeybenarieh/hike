"""Tests for Entity/UuidEntity based on examples/entities.py and examples/overall.py."""

from typing import assert_type
from uuid import UUID

from cliff.ddd.entity import EntityID, Field, UuidEntity
from cliff.ddd.value_object import ValueObject
from cliff.ddd.specifications import (
    AndSpecification,
    EqualSpecification,
    LessThanSpecification,
    LessThanEqualSpecification,
    GreaterThanSpecification,
    GreaterThanEqualSpecification,
    NotEqualSpecification,
    NotSpecification,
    OrSpecification,
)


class Name(ValueObject[str]):
    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("Name cannot be empty")


class Price(ValueObject[float]):
    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("Price cannot be negative")


class Ship(UuidEntity):
    name: Field[Name]


class Boat(UuidEntity):
    name: str
    price: Field[Price]

    def discount_price(self) -> Price:
        return Price(self.price.value * 0.9)


class TestUuidEntityIdentity:
    def test_auto_generated_uuid(self) -> None:
        s = Ship(name=Name("Titanic"))
        assert isinstance(s.id, EntityID)
        assert isinstance(s.id.value, UUID)

    def test_two_instances_have_different_ids(self) -> None:
        s1 = Ship(name=Name("Titanic"))
        s2 = Ship(name=Name("Titanic"))
        assert s1.id != s2.id

    def test_different_ids_not_equal(self) -> None:
        s1 = Ship(name=Name("Titanic"))
        s2 = Ship(name=Name("Titanic"))
        assert s1 != s2

    def test_same_id_equal(self) -> None:
        s1 = Ship(name=Name("Titanic"))
        s3 = Ship(id=s1.id, name=Name("Renamed"))
        assert s1 == s3

    def test_same_id_equal_despite_different_fields(self) -> None:
        s1 = Ship(name=Name("Titanic"))
        s3 = Ship(id=s1.id, name=Name("Olympic"))
        assert s1 == s3

    def test_different_type_not_equal(self) -> None:
        s = Ship(name=Name("Titanic"))
        b = Boat(id=s.id, name="Titanic", price=Price(100))
        assert s != b

    def test_entity_hashable(self) -> None:
        s1 = Ship(name=Name("Titanic"))
        s2 = Ship(name=Name("Olympic"))
        # Verify hash() works and produces distinct values for distinct entities
        assert hash(s1) != hash(s2)

    def test_same_id_same_hash(self) -> None:
        s1 = Ship(name=Name("Titanic"))
        s3 = Ship(id=s1.id, name=Name("Renamed"))
        assert hash(s1) == hash(s3)


class TestFieldDescriptor:
    def test_instance_access_returns_value_object(self) -> None:
        s = Ship(name=Name("Titanic"))
        assert isinstance(s.name, Name)
        assert s.name.value == "Titanic"

    def test_class_access_returns_field_proxy(self) -> None:
        from cliff.ddd.entity import FieldProxy
        assert isinstance(Ship.name, FieldProxy)

    def test_raw_value_auto_converted(self) -> None:
        # Field[Name] should auto-convert raw str to Name
        s = Ship(name="Titanic")  # pyright: ignore[reportArgumentType]
        assert isinstance(s.name, Name)

    def test_price_field_on_boat(self) -> None:
        b = Boat(name="My Boat", price=Price(100))
        assert b.price.value == 100.0

    def test_discount_price_method(self) -> None:
        b = Boat(name="My Boat", price=Price(100))
        discounted = b.discount_price()
        assert isinstance(discounted, Price)
        assert abs(discounted.value - 90.0) < 1e-9

    def test_plain_field_unchanged(self) -> None:
        b = Boat(name="My Boat", price=Price(100))
        assert b.name == "My Boat"


class TestFieldProxySpecifications:
    def test_class_eq_produces_equal_spec(self) -> None:
        spec = Ship.name == "Titanic"
        assert isinstance(spec, EqualSpecification)
        assert spec.operand == "Titanic"

    def test_class_ne_produces_not_equal_spec(self) -> None:
        spec = Ship.name != "Titanic"
        assert isinstance(spec, NotEqualSpecification)
        assert spec.operand == "Titanic"

    def test_class_lt_produces_less_than_spec(self) -> None:
        spec = Boat.price < 100
        assert isinstance(spec, LessThanSpecification)
        assert spec.operand == 100

    def test_class_le_produces_less_than_equal_spec(self) -> None:
        spec = Boat.price <= 100
        assert isinstance(spec, LessThanEqualSpecification)

    def test_class_gt_produces_greater_than_spec(self) -> None:
        spec = Boat.price > 50
        assert isinstance(spec, GreaterThanSpecification)

    def test_class_ge_produces_greater_than_equal_spec(self) -> None:
        spec = Boat.price >= 50
        assert isinstance(spec, GreaterThanEqualSpecification)

    def test_spec_field_name(self) -> None:
        spec = Ship.name == "Titanic"
        assert spec.field.field_name == "name"

    def test_instance_lt_returns_bool(self) -> None:
        b = Boat(name="My Boat", price=Price(50))
        assert b.price < 100
        assert not (b.price < 10)


class TestStaticTypes:
    """Verify that Pyright infers the correct return types for FieldProxy operators.

    assert_type() is a no-op at runtime (always passes) but Pyright raises a
    type error if the inferred type does not exactly match the declared one.
    """

    def test_lt_is_less_than_specification(self) -> None:
        assert_type(Boat.price < 5, LessThanSpecification)

    def test_le_is_less_than_equal_specification(self) -> None:
        assert_type(Boat.price <= 5, LessThanEqualSpecification)

    def test_gt_is_greater_than_specification(self) -> None:
        assert_type(Boat.price > 5, GreaterThanSpecification)

    def test_ge_is_greater_than_equal_specification(self) -> None:
        assert_type(Boat.price >= 5, GreaterThanEqualSpecification)

    def test_eq_is_equal_specification(self) -> None:
        assert_type(Boat.price == 5, EqualSpecification)

    def test_ne_is_not_equal_specification(self) -> None:
        assert_type(Boat.price != 5, NotEqualSpecification)

    def test_and_composition_is_and_specification(self) -> None:
        spec = (Boat.price < 100) & (Boat.price > 10)
        assert_type(spec, AndSpecification)

    def test_or_composition_is_or_specification(self) -> None:
        spec = (Boat.price < 100) | (Boat.price > 200)
        assert_type(spec, OrSpecification)

    def test_not_composition_is_not_specification(self) -> None:
        spec = ~(Boat.price == 50)
        assert_type(spec, NotSpecification)

    def test_instance_field_access_is_value_object(self) -> None:
        b = Boat(name="My Boat", price=Price(100))
        assert_type(b.price, Price)

    def test_instance_comparison_is_bool(self) -> None:
        b = Boat(name="My Boat", price=Price(50))
        assert_type(b.price < 100, bool)
