"""Tests for Aggregate, UuidAggregate, Rule, RuleBrokenError, and domain events."""
from __future__ import annotations

from uuid import UUID

import pytest

from hike.ddd.aggregate import Rule, RuleBrokenError, UuidAggregate
from hike.ddd.domain_event import DomainEvent
from hike.ddd.entity import EntityID, Field
from hike.ddd.value_object import ValueObject

from tests.hike.ddd.conftest import Boat, Name, Price


# ---------------------------------------------------------------------------
# Test-local aggregate and events
#
# DomainEvent tests require an aggregate that raises events through its own
# methods — accessing _events directly from outside would be a protected-attr
# violation and doesn't reflect real usage.
# ---------------------------------------------------------------------------


class ShipName(ValueObject[str]): ...


class ShipSold(DomainEvent):
    pass


class Ship(UuidAggregate):
    name: Field[ShipName]

    def sell(self) -> None:
        self._events.append(ShipSold())


# ---------------------------------------------------------------------------
# Business rules for Boat
# ---------------------------------------------------------------------------


class PriceAboveRule(Rule[Boat]):
    """Broken when price exceeds the ceiling."""

    def __init__(self, ceiling: float) -> None:
        self.ceiling = ceiling

    def is_broken(self, obj: Boat) -> bool:
        return obj.price.value > self.ceiling


class PriceBelowRule(Rule[Boat]):
    """Broken when price is below the floor."""

    def __init__(self, floor: float) -> None:
        self.floor = floor

    def is_broken(self, obj: Boat) -> bool:
        return obj.price.value < self.floor


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


class TestCheckInvariants:
    def test_empty_rule_list_passes(self, boat: Boat) -> None:
        boat.check_invariants([])

    def test_all_rules_satisfied_passes(self) -> None:
        boat = Boat(name=Name("Mid-range"), price=Price(100.0))
        # Neither rule is broken: 100 is not below 50, and not above 200.
        boat.check_invariants([PriceBelowRule(50.0), PriceAboveRule(200.0)])

    def test_broken_rule_raises(self) -> None:
        cheap = Boat(name=Name("Budget"), price=Price(10.0))
        with pytest.raises(RuleBrokenError):
            cheap.check_invariants([PriceAboveRule(5.0), PriceBelowRule(20.0)])

    def test_stops_at_first_broken_rule(self) -> None:
        boat = Boat(name=Name("Any"), price=Price(10.0))
        evaluated: list[int] = []

        class TrackedRule(Rule[Boat]):
            def __init__(self, idx: int, broken: bool) -> None:
                self._idx = idx
                self._broken = broken

            def is_broken(self, obj: Boat) -> bool:  # noqa: ARG002
                evaluated.append(self._idx)
                return self._broken

        with pytest.raises(RuleBrokenError):
            boat.check_invariants([TrackedRule(0, True), TrackedRule(1, False)])
        assert evaluated == [0]


class TestUuidAggregate:
    def test_id_is_uuid(self, boat: Boat) -> None:
        assert isinstance(boat.id.value, UUID)

    def test_two_instances_have_different_ids(self) -> None:
        b1 = Boat(name=Name("A"), price=Price(1.0))
        b2 = Boat(name=Name("B"), price=Price(2.0))
        assert b1.id != b2.id
        assert b1 != b2

    def test_explicit_id_is_preserved(self) -> None:
        eid = EntityID(UUID("12345678-1234-5678-1234-567812345678"))
        boat = Boat(id=eid, name=Name("Named"), price=Price(1.0))
        assert boat.id == eid

    def test_same_id_means_equal(self, boat: Boat) -> None:
        clone = Boat(id=boat.id, name=Name("Renamed"), price=Price(1.0))
        assert boat == clone

    def test_has_domain_events(self, boat: Boat) -> None:
        assert isinstance(boat.get_events(), list)
