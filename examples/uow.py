"""Unit of Work example with in-memory storage.

Demonstrates the UnitOfWork pattern using InMemoryDBContext and
InMemoryRepository — no external dependencies required.

Run with::

    uv run python examples/uow.py
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from hike import Aggregate, AggregateDoesNotExistError, Field, UnitOfWork, UuidAggregate, UuidEntity, ValueObject
from hike.ddd.providers.in_memory import InMemoryDBContext, InMemoryRepository


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


class Price(ValueObject[float]):
    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("Price cannot be negative")


class EngineName(ValueObject[str]):
    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("Engine name cannot be empty")


class BoatName(ValueObject[str]):
    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("Boat name cannot be empty")


class Engine(UuidEntity):
    name: Field[EngineName]
    price: Field[Price]


class Boat(UuidAggregate):
    name: Field[BoatName]
    engine: Field[Engine]
    price: Field[Price]


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

context = InMemoryDBContext()
repo: InMemoryRepository[UUID, Boat] = InMemoryRepository()
uow: UnitOfWork[dict[Any, Aggregate[Any]], UUID, Boat] = UnitOfWork(context, repo=repo)

# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------
# TODO: enforce the engine price is lower than the total boat cost
default_engine = Engine(name=EngineName("my engine"), price=Price(1_000.99))
boat = Boat(name=BoatName("Sea Spirit"), price=Price(4_999.99), engine=default_engine)

with uow:
    uow.repo.save(boat)
    uow.commit()

print(f"Saved: {boat.name.value} (id={boat.id.value})")

# ---------------------------------------------------------------------------
# Query with a specification
# ---------------------------------------------------------------------------

cheap = Boat(name=BoatName("Dinghy"), price=Price(299.0), engine=default_engine)
with uow:
    uow.repo.save(cheap)
    uow.commit()

with uow:
    results = uow.repo.get_many(Boat.engine.price > 1_000.0)

print(f"Boats priced above 1000: {[b.name.value for b in results]}")

# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

boat.price = Price(3_999.99)
with uow:
    uow.repo.update(boat)
    uow.commit()

with uow:
    fetched = uow.repo.get_one(boat.id)
print(f"Updated price: {fetched.price.value}")

# ---------------------------------------------------------------------------
# Rollback on error
# ---------------------------------------------------------------------------

ghost = Boat(name=BoatName("Ghost"), price=Price(1.0), engine=default_engine)
try:
    with uow:
        uow.repo.save(ghost)
        raise RuntimeError("something went wrong")
except RuntimeError:
    pass

with uow:
    try:
        uow.repo.get_one(ghost.id)
        print("ERROR: ghost should not exist")
    except AggregateDoesNotExistError:
        print("Rollback confirmed: ghost was not persisted")
