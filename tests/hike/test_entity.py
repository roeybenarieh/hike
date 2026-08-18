"""Tests for Entity, Field descriptor, FieldProxy, and to_dict utilities."""
from __future__ import annotations

from typing import assert_type
from uuid import UUID

from hike.entity import from_dict, get_fields, to_dict
from hike.specifications import (
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

from tests.hike.conftest import Boat, BoatEngine, Engine, Horsepower, MotorBoat, Name, Price


class TestUuidEntityIdentity:
    def test_auto_generated_uuid(self) -> None:
        e = Engine(name=Name("Titanic"), horsepower=Horsepower(100))
        from hike.entity import EntityID
        assert isinstance(e.id, EntityID)
        assert isinstance(e.id.value, UUID)

    def test_two_instances_have_different_ids(self) -> None:
        e1 = Engine(name=Name("Titanic"), horsepower=Horsepower(100))
        e2 = Engine(name=Name("Titanic"), horsepower=Horsepower(100))
        assert e1.id != e2.id

    def test_different_ids_not_equal(self) -> None:
        e1 = Engine(name=Name("Titanic"), horsepower=Horsepower(100))
        e2 = Engine(name=Name("Titanic"), horsepower=Horsepower(100))
        assert e1 != e2

    def test_same_id_equal(self) -> None:
        e1 = Engine(name=Name("Titanic"), horsepower=Horsepower(100))
        e2 = Engine(id=e1.id, name=Name("Renamed"), horsepower=Horsepower(200))
        assert e1 == e2

    def test_same_id_equal_despite_different_fields(self) -> None:
        e1 = Engine(name=Name("V8"), horsepower=Horsepower(200))
        e2 = Engine(id=e1.id, name=Name("V6"), horsepower=Horsepower(150))
        assert e1 == e2

    def test_different_type_not_equal(self) -> None:
        e = Engine(name=Name("V8"), horsepower=Horsepower(200))
        b = Boat(id=e.id, name=Name("Sea Spirit"), price=Price(100))
        assert e != b

    def test_entity_hashable(self) -> None:
        e1 = Engine(name=Name("V8"), horsepower=Horsepower(200))
        e2 = Engine(name=Name("V6"), horsepower=Horsepower(150))
        assert hash(e1) != hash(e2)

    def test_same_id_same_hash(self) -> None:
        e1 = Engine(name=Name("V8"), horsepower=Horsepower(200))
        e2 = Engine(id=e1.id, name=Name("Renamed"), horsepower=Horsepower(100))
        assert hash(e1) == hash(e2)


class TestFieldDescriptor:
    def test_instance_access_returns_value_object(self) -> None:
        e = Engine(name=Name("Titanic"), horsepower=Horsepower(100))
        assert isinstance(e.name, Name)
        assert e.name.value == "Titanic"

    def test_class_access_returns_field_proxy(self) -> None:
        from hike.entity import FieldProxy
        assert isinstance(Engine.name, FieldProxy)

    def test_raw_value_auto_converted(self) -> None:
        # Field[Name] auto-converts raw str to Name
        e = Engine(name="Titanic", horsepower=Horsepower(100))  # pyright: ignore[reportArgumentType]
        assert isinstance(e.name, Name)

    def test_price_field_on_boat(self) -> None:
        b = Boat(name=Name("My Boat"), price=Price(100))
        assert b.price.value == 100.0

    def test_discount_price_method(self) -> None:
        b = Boat(name=Name("My Boat"), price=Price(100))
        discounted = b.discount_price()
        assert isinstance(discounted, Price)
        assert abs(discounted.value - 90.0) < 1e-9

    def test_horsepower_field_is_value_object(self) -> None:
        e = Engine(name=Name("V8"), horsepower=Horsepower(200))
        assert isinstance(e.horsepower, Horsepower)
        assert e.horsepower.value == 200


class TestFieldProxySpecifications:
    def test_class_eq_produces_equal_spec(self) -> None:
        spec = Engine.name == "Titanic"
        assert isinstance(spec, EqualSpecification)
        assert spec.operand == "Titanic"

    def test_class_ne_produces_not_equal_spec(self) -> None:
        spec = Engine.name != "Titanic"
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
        spec = Engine.name == "Titanic"
        assert spec.field.field_name == "name"

    def test_instance_lt_returns_bool(self) -> None:
        b = Boat(name=Name("My Boat"), price=Price(50))
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
        b = Boat(name=Name("My Boat"), price=Price(100))
        assert_type(b.price, Price)

    def test_instance_comparison_is_bool(self) -> None:
        b = Boat(name=Name("My Boat"), price=Price(50))
        assert_type(b.price < 100, bool)


class TestToDict:
    def test_non_vo_field_is_kept_as_is(self) -> None:
        e = Engine(name=Name("V8"), horsepower=Horsepower(200))
        assert to_dict(e)["horsepower"] == 200

    def test_value_object_name_field_is_flattened(self) -> None:
        e = Engine(name=Name("V8"), horsepower=Horsepower(200))
        assert to_dict(e)["name"] == "V8"

    def test_value_object_price_field_is_flattened(self) -> None:
        b = Boat(name=Name("My Boat"), price=Price(100.0))
        assert to_dict(b)["price"] == 100.0

    def test_id_field_is_flattened_to_uuid(self) -> None:
        b = Boat(name=Name("My Boat"), price=Price(100.0))
        d = to_dict(b)
        assert isinstance(d["id"], UUID)
        assert d["id"] == b.id.value

    def test_entity_init_fields_are_present(self) -> None:
        e = Engine(name=Name("V8"), horsepower=Horsepower(200))
        assert set(to_dict(e).keys()) == {"id", "name", "horsepower"}

    def test_aggregate_init_fields_are_present(self) -> None:
        b = Boat(name=Name("My Boat"), price=Price(100.0))
        assert set(to_dict(b).keys()) == {"id", "name", "price"}

    def test_get_fields_returns_dataclass_fields(self) -> None:
        # Engine (UuidEntity) has no non-init fields, so the set is exact.
        e = Engine(name=Name("V8"), horsepower=Horsepower(200))
        assert {f.name for f in get_fields(e)} == {"id", "name", "horsepower"}


class TestToDictFromDictNestedEntity:
    """Regression tests for the to_dict / from_dict Field[Entity] bug.

    Before the fix, to_dict stored a ReadOnlyView proxy for Field[Entity] fields
    instead of recursing into the nested entity, making the result
    non-serialisable and from_dict unable to reconstruct the object.
    """

    def _make_motorboat(self) -> MotorBoat:
        eng = BoatEngine(name=Name("V8 Turbo"), price=Price(1_500.0))
        return MotorBoat(name=Name("Sea Spirit"), price=Price(9_999.0), engine=eng)

    def test_to_dict_nested_entity_is_dict_not_proxy(self) -> None:
        boat = self._make_motorboat()
        d = to_dict(boat)
        assert isinstance(d["engine"], dict), (
            "to_dict must recurse into Field[Entity] and produce a plain dict, "
            f"got {type(d['engine'])!r} instead"
        )

    def test_to_dict_nested_entity_contains_expected_keys(self) -> None:
        from typing import Any, cast
        boat = self._make_motorboat()
        engine_dict = cast(dict[str, Any], to_dict(boat)["engine"])
        assert isinstance(engine_dict, dict)
        assert set(engine_dict.keys()) == {"id", "name", "price"}

    def test_to_dict_nested_entity_values_are_scalars(self) -> None:
        boat = self._make_motorboat()
        engine_dict = to_dict(boat)["engine"]
        assert isinstance(engine_dict, dict)
        assert engine_dict["name"] == "V8 Turbo"
        assert engine_dict["price"] == 1_500.0

    def test_from_dict_round_trips_nested_entity(self) -> None:
        boat = self._make_motorboat()
        d = to_dict(boat)
        restored: MotorBoat = from_dict(MotorBoat, d)
        assert restored.id == boat.id
        assert restored.name.value == boat.name.value
        assert restored.price.value == boat.price.value
        assert restored.engine.id == boat.engine.id
        assert restored.engine.name.value == boat.engine.name.value
        assert restored.engine.price.value == boat.engine.price.value
