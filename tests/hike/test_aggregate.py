"""Tests for Aggregate, UuidAggregate, Rule, RuleBrokenError, and domain events."""
from __future__ import annotations

from uuid import UUID

import pytest

from hike.aggregate import UuidAggregate
from hike.domain_event import DomainEvent
from hike.entity import EntityUUID, Field
from hike.rules import Rule, RuleBrokenError, SpecificationRule
from hike.value_object import ValueObject
from tests.hike.conftest import Boat, Name, Price


# ---------------------------------------------------------------------------
# Test-local aggregate and events
#
# DomainEvent tests require an aggregate that raises events through its own
# methods — the internal event list is name-mangled (__events) and inaccessible
# outside the Aggregate class body.
# ---------------------------------------------------------------------------


class ShipName(ValueObject[str]): ...


class ShipSold(DomainEvent):
    pass


class Ship(UuidAggregate):
    name: Field[ShipName]

    def sell(self) -> None:
        self.raise_event(ShipSold())


# ---------------------------------------------------------------------------
# Business rules for Boat
# ---------------------------------------------------------------------------


class PriceAboveRule(Rule[Boat]):
    """Broken when price exceeds the ceiling."""

    def __init__(self, ceiling: float) -> None:
        self.ceiling = ceiling

    def is_broken(self, obj: Boat) -> bool:
        return obj.price > self.ceiling


class PriceBelowRule(Rule[Boat]):
    """Broken when price is below the floor."""

    def __init__(self, floor: float) -> None:
        self.floor = floor

    def is_broken(self, obj: Boat) -> bool:
        return obj.price < self.floor


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDomainEvents:
    def test_new_aggregate_has_no_events(self) -> None:
        ship = Ship(name=ShipName("Titanic"))
        assert ship.get_events() == []

    def test_action_records_event(self) -> None:
        ship = Ship(name=ShipName("Titanic"))
        ship.sell()
        events = ship.get_events()
        assert len(events) == 1
        assert isinstance(events[0], ShipSold)

    def test_multiple_events_preserved_in_order(self) -> None:
        ship = Ship(name=ShipName("Titanic"))
        ship.sell()
        ship.sell()
        assert len(ship.get_events()) == 2

    def test_get_events_returns_a_copy(self) -> None:
        ship = Ship(name=ShipName("Titanic"))
        ship.sell()
        snapshot = ship.get_events()
        snapshot.clear()
        assert len(ship.get_events()) == 1

    def test_clear_events_empties_list(self) -> None:
        ship = Ship(name=ShipName("Titanic"))
        ship.sell()
        ship.sell()
        ship.clear_events()
        assert ship.get_events() == []

    def test_events_not_shared_across_instances(self) -> None:
        s1 = Ship(name=ShipName("Alpha"))
        s2 = Ship(name=ShipName("Beta"))
        s1.sell()
        assert s2.get_events() == []


class TestRule:
    def test_satisfied_rule_does_not_raise(self, boat: Boat) -> None:
        PriceAboveRule(10_000.0).raise_on_broken_rule(boat)

    def test_broken_rule_raises_rule_broken_error(self) -> None:
        yacht = Boat(name=Name("Yacht"), price=Price(500.0))
        with pytest.raises(RuleBrokenError):
            PriceAboveRule(100.0).raise_on_broken_rule(yacht)

    def test_rule_broken_error_carries_the_rule(self) -> None:
        yacht = Boat(name=Name("Yacht"), price=Price(500.0))
        rule = PriceAboveRule(100.0)
        with pytest.raises(RuleBrokenError) as exc_info:
            rule.raise_on_broken_rule(yacht)
        assert exc_info.value.broken_rule is rule


class TestSpecificationRule:
    def test_satisfied_spec_does_not_raise(self, boat: Boat) -> None:
        spec = Boat.price > 1_000.0
        rule: SpecificationRule[Boat] = SpecificationRule(spec)
        rule.raise_on_broken_rule(boat)

    def test_unsatisfied_spec_raises(self, boat: Boat) -> None:
        spec = Boat.price > 10_000.0
        rule: SpecificationRule[Boat] = SpecificationRule(spec)
        with pytest.raises(RuleBrokenError):
            rule.raise_on_broken_rule(boat)

    def test_error_carries_rule(self, boat: Boat) -> None:
        spec = Boat.price > 10_000.0
        sr: SpecificationRule[Boat] = SpecificationRule(spec)
        with pytest.raises(RuleBrokenError) as exc_info:
            sr.raise_on_broken_rule(boat)
        assert exc_info.value.broken_rule is sr

    def test_is_broken_false_when_satisfied(self, boat: Boat) -> None:
        spec = Boat.price > 1_000.0
        rule: SpecificationRule[Boat] = SpecificationRule(spec)
        assert rule.is_broken(boat) is False

    def test_is_broken_true_when_not_satisfied(self, boat: Boat) -> None:
        spec = Boat.price > 10_000.0
        rule: SpecificationRule[Boat] = SpecificationRule(spec)
        assert rule.is_broken(boat) is True

    def test_and_spec_both_true_does_not_raise(self, boat: Boat) -> None:
        spec = (Boat.price > 1_000.0) & (Boat.name == "Sea Spirit")
        rule: SpecificationRule[Boat] = SpecificationRule(spec)
        rule.raise_on_broken_rule(boat)

    def test_and_spec_one_false_raises(self, boat: Boat) -> None:
        spec = (Boat.price > 1_000.0) & (Boat.name == "Wrong Name")
        rule: SpecificationRule[Boat] = SpecificationRule(spec)
        with pytest.raises(RuleBrokenError):
            rule.raise_on_broken_rule(boat)

    def test_or_spec_one_true_does_not_raise(self, boat: Boat) -> None:
        spec = (Boat.price > 10_000.0) | (Boat.name == "Sea Spirit")
        rule: SpecificationRule[Boat] = SpecificationRule(spec)
        rule.raise_on_broken_rule(boat)

    def test_not_spec_inverts_result(self, boat: Boat) -> None:
        spec = ~(Boat.price > 10_000.0)
        rule: SpecificationRule[Boat] = SpecificationRule(spec)
        rule.raise_on_broken_rule(boat)

    def test_value_object_field_unwrapped(self, boat: Boat) -> None:
        # Price is a ValueObject[float]; evaluator must unwrap .value for comparison
        spec = Boat.price == 4_999.99
        rule: SpecificationRule[Boat] = SpecificationRule(spec)
        assert rule.is_broken(boat) is False


class TestUuidAggregate:
    def test_id_is_uuid(self, boat: Boat) -> None:
        assert isinstance(boat.id.value, UUID)

    def test_two_instances_have_different_ids(self) -> None:
        b1 = Boat(name=Name("A"), price=Price(1.0))
        b2 = Boat(name=Name("B"), price=Price(2.0))
        assert b1.id != b2.id
        assert b1 != b2

    def test_explicit_id_is_preserved(self) -> None:
        eid = EntityUUID(UUID("12345678-1234-5678-1234-567812345678"))
        boat = Boat(id=eid, name=Name("Named"), price=Price(1.0))
        assert boat.id == eid

    def test_same_id_means_equal(self, boat: Boat) -> None:
        clone = Boat(id=boat.id, name=Name("Renamed"), price=Price(1.0))
        assert boat == clone

    def test_has_domain_events(self, boat: Boat) -> None:
        assert isinstance(boat.get_events(), list)
