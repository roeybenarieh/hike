"""Shared domain model and pytest fixtures for DDD tests.

All classes below are importable by any test module under ``tests/hike/ddd/``::

    from tests.hike.ddd.conftest import Boat, BoatEngine, Category, Engine, \
        ListingName, MotorBoat, Name, Price, ProductListing, Rating

The ``boat``, ``engine``, and ``make_boat`` fixtures are auto-discovered by
pytest and available to all tests under this directory, including provider
integration tests.
"""
from __future__ import annotations

from collections.abc import Callable

import pytest

from hike.ddd.aggregate import UuidAggregate
from hike.ddd.entity import Field, UuidEntity
from hike.ddd.value_object import ValueObject


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


class Price(ValueObject[float]):
    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("Price cannot be negative")


class Name(ValueObject[str]):
    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("Name cannot be empty")


class Category(ValueObject[str]):
    pass


class Rating(ValueObject[float]):
    def __post_init__(self) -> None:
        if not (0.0 <= self.value <= 5.0):
            raise ValueError("Rating must be between 0 and 5")


class ListingName(ValueObject[str]):
    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("Name cannot be empty")


# ---------------------------------------------------------------------------
# Entities (have identity, but are not aggregate roots)
# ---------------------------------------------------------------------------


class Engine(UuidEntity):
    name: Field[Name]
    horsepower: int


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


class MotorBoat(UuidAggregate):
    name: str
    price: Field[Price]
    engine: Field[BoatEngine]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Engine:
    return Engine(name=Name("V8"), horsepower=200)


@pytest.fixture
def boat() -> Boat:
    return Boat(name=Name("Sea Spirit"), price=Price(4_999.99))


@pytest.fixture
def make_boat() -> Callable[..., Boat]:
    """Factory fixture — call to create a Boat with a custom name/price."""
    def _make(name: str = "Sea Spirit", price: float = 4_999.99) -> Boat:
        return Boat(name=Name(name), price=Price(price))
    return _make
