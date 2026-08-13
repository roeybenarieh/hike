"""Shared domain model and pytest fixtures for DDD tests.

All classes below are importable by any test module under ``tests/hike/ddd/``::

    from tests.hike.ddd.conftest import Boat, BoatEngine, Category, Checkpoint, \
        Engine, Horsepower, Journey, ListingName, MotorBoat, Name, Price, \
        ProductListing, Rating

The ``boat``, ``engine``, and ``make_boat`` fixtures are auto-discovered by
pytest and available to all tests under this directory, including provider
integration tests.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import field

import pytest

from hike.ddd.aggregate import UuidAggregate
from hike.ddd.entity import Field, UuidEntity
from hike.ddd.value_object import ValueObject, between, non_empty, non_negative


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


class Price(ValueObject[float]):
    __validators__ = [non_negative]


class Name(ValueObject[str]):
    __validators__ = [non_empty]


class Horsepower(ValueObject[int]):
    __validators__ = [non_negative]


class Category(ValueObject[str]):
    pass


class Rating(ValueObject[float]):
    __validators__ = [between(0.0, 5.0)]


class ListingName(ValueObject[str]):
    __validators__ = [non_empty]


# ---------------------------------------------------------------------------
# Entities (have identity, but are not aggregate roots)
# ---------------------------------------------------------------------------


class Engine(UuidEntity):
    name: Field[Name]
    horsepower: Field[Horsepower]


class Checkpoint(UuidEntity):
    name: Field[Name]


class BoatEngine(UuidEntity):
    name: Field[Name]
    price: Field[Price]


class ProductListing(UuidEntity):
    price: Field[Price]
    category: Field[Category]
    rating: Field[Rating]
    name: Field[ListingName]


# ---------------------------------------------------------------------------
# Aggregate roots
# ---------------------------------------------------------------------------


class Boat(UuidAggregate):
    name: Field[Name]
    price: Field[Price]

    def discount_price(self) -> Price:
        return Price(self.price.value * 0.9)


class Journey(UuidAggregate):
    name: Field[Name]
    checkpoints: list[Checkpoint] = field(default_factory=list)


class MotorBoat(UuidAggregate):
    name: Field[Name]
    price: Field[Price]
    engine: Field[BoatEngine]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Engine:
    return Engine(name=Name("V8"), horsepower=Horsepower(200))


@pytest.fixture
def boat() -> Boat:
    return Boat(name=Name("Sea Spirit"), price=Price(4_999.99))


@pytest.fixture
def make_boat() -> Callable[..., Boat]:
    """Factory fixture — call to create a Boat with a custom name/price."""
    def _make(name: str = "Sea Spirit", price: float = 4_999.99) -> Boat:
        return Boat(name=Name(name), price=Price(price))
    return _make
