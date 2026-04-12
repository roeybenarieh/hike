"""Unit of Work example with in-memory storage.

Demonstrates the UnitOfWork pattern using InMemoryDBContext and
InMemoryRepository — no external dependencies required.

Run with::

    uv run python examples/uow.py
"""
from __future__ import annotations

from typing import cast

from cliff.ddd.aggregate import UuidAggregate
from cliff.ddd.uow import InMemoryDBContext
from cliff.ddd.entity import Field
from cliff.ddd.repository import AggregateDoesNotExistError, InMemoryRepository
from cliff.ddd.uow import UnitOfWork
from cliff.ddd.value_object import ValueObject


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


class Price(ValueObject[float]):
    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("Price cannot be negative")


class Boat(UuidAggregate):
    name: str
    price: Field[Price]


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

context = InMemoryDBContext()
repo: InMemoryRepository[object] = InMemoryRepository()
uow: UnitOfWork[dict, object] = UnitOfWork(context, repo=repo)

# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

boat = Boat(name="Sea Spirit", price=Price(4_999.99))

with uow:
    uow.repo.save(boat)
    uow.commit()

print(f"Saved: {boat.name} (id={boat.id.value})")

# ---------------------------------------------------------------------------
# Query with a specification
# ---------------------------------------------------------------------------

cheap = Boat(name="Dinghy", price=Price(299.0))
with uow:
    uow.repo.save(cheap)
    uow.commit()

with uow:
    results = uow.repo.get_many(Boat.price > 1_000.0)

print(f"Boats priced above 1000: {[cast(Boat, b).name for b in results]}")

# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

boat.price = Price(3_999.99)  # type: ignore[assignment]
with uow:
    uow.repo.update(boat)
    uow.commit()

with uow:
    fetched = cast(Boat, uow.repo.get_one(boat.id.value))
print(f"Updated price: {fetched.price.value}")

# ---------------------------------------------------------------------------
# Rollback on error
# ---------------------------------------------------------------------------

ghost = Boat(name="Ghost", price=Price(1.0))
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
