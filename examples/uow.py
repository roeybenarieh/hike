"""Unit of Work example with in-memory storage.

Demonstrates the UnitOfWork pattern using InMemoryDBContext and
InMemoryRepository — no external dependencies required.

Run with::

    uv run python examples/uow.py
"""
from __future__ import annotations

from typing import cast

from hike.ddd.aggregate import UuidAggregate
from hike.ddd.entity import UuidEntity
from hike.ddd.common import DomainField
from hike.ddd.repository import AggregateDoesNotExistError, InMemoryRepository
from hike.ddd.uow import InMemoryDBContext
from hike.ddd.uow import UnitOfWork
from hike.ddd.value_object import ValueObject


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


class Engine(UuidEntity):
    name: DomainField[EngineName]
    price: DomainField[Price]


class Boat(UuidAggregate):
    name: str
    engine: DomainField[Engine]
    price: DomainField[Price]


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

context = InMemoryDBContext()
repo: InMemoryRepository[object] = InMemoryRepository()
uow: UnitOfWork[dict, object] = UnitOfWork(context, repo=repo)

# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------
# TODO: enforce the engine price is lower than the total boat cost
default_engine = Engine(name=EngineName("my engine"), price=Price(1_000.99))
boat = Boat(name="Sea Spirit", price=Price(4_999.99), engine=default_engine)

with uow:
    uow.repo.save(boat)
    uow.commit()

print(f"Saved: {boat.name} (id={boat.id.value})")

# ---------------------------------------------------------------------------
# Query with a specification
# ---------------------------------------------------------------------------

cheap = Boat(name="Dinghy", price=Price(299.0), engine=default_engine)
with uow:
    uow.repo.save(cheap)
    uow.commit()

with uow:
    results = uow.repo.get_many(Boat.engine.price > 1_000.0)

print(f"Boats priced above 1000: {[cast(Boat, b).name for b in results]}")

# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

boat.price = Price(3_999.99)
with uow:
    uow.repo.update(boat)
    uow.commit()

with uow:
    fetched = cast(Boat, uow.repo.get_one(boat.id.value))
print(f"Updated price: {fetched.price.value}")

# ---------------------------------------------------------------------------
# Rollback on error
# ---------------------------------------------------------------------------

ghost = Boat(name="Ghost", price=Price(1.0), engine=default_engine)
try:
    with uow:
        uow.repo.save(ghost)
        raise RuntimeError("something went wrong")
except RuntimeError:
    pass

with uow:
    try:
        uow.repo.get_one(ghost.id.value)
        print("ERROR: ghost should not exist")
    except AggregateDoesNotExistError:
        print("Rollback confirmed: ghost was not persisted")
