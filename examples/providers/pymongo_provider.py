"""PyMongo provider example.

Demonstrates save, query, update, and rollback using MongoDB as the
backing store via ``PyMongoDBContext`` and ``PyMongoRepository``.

Requires a MongoDB replica set running locally (replica set is required
for multi-document ACID transactions).

Start with devenv::

    devenv up mongodb

Run with::

    uv run python examples/providers/pymongo_provider.py
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.synchronous.client_session import ClientSession

from hike import (
    AggregateDoesNotExistError,
    Field,
    UnitOfWork,
    UuidAggregate,
    UuidEntity,
    ValueObject,
    non_empty,
    non_negative,
    rule,
)
from hike.persistence.providers.pymongo import PyMongoDBContext, PyMongoRepository


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


class Price(ValueObject[float]):
    __validators__ = [non_negative]


class EngineName(ValueObject[str]):
    __validators__ = [non_empty]


class BoatName(ValueObject[str]):
    __validators__ = [non_empty]


class Engine(UuidEntity):
    name: Field[EngineName]
    price: Field[Price]


@rule(message="Engine price must not exceed the boat price")
def engine_price_within_boat_price(boat: "Boat") -> bool:
    return boat.engine.price.value > boat.price.value


class Boat(UuidAggregate):
    name: Field[BoatName]
    engine: Field[Engine]
    price: Field[Price]
    __invariants__ = [engine_price_within_boat_price]


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

client: MongoClient[dict[str, Any]] = MongoClient(
    "mongodb://admin:secret@127.0.0.1:27017/?authSource=admin&directConnection=true"
)
ctx = PyMongoDBContext(client)
collection: Collection[dict[str, Any]] = ctx.client["hike"]["boats"]
ctx.client["hike"].drop_collection("boats")  # start clean for a repeatable demo

repo: PyMongoRepository[UUID, Boat] = PyMongoRepository(collection, Boat)
uow: UnitOfWork[ClientSession] = UnitOfWork(ctx)

# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

engine = Engine(name=EngineName("V8 Turbo"), price=Price(1_500.0))
boat = Boat(name=BoatName("Sea Spirit"), price=Price(9_999.0), engine=engine)

with uow(repo):
    repo.save(boat)
    uow.commit()

print(f"Saved:   {boat.name.value!r} (id={boat.id.value})")

# ---------------------------------------------------------------------------
# Query with a specification
# ---------------------------------------------------------------------------

budget_engine = Engine(name=EngineName("40hp Outboard"), price=Price(400.0))
budget_boat = Boat(name=BoatName("Dinghy"), price=Price(799.0), engine=budget_engine)

with uow(repo):
    repo.save(budget_boat)
    uow.commit()

with uow(repo):
    expensive = repo.get_many(Boat.price > 5_000.0)

print(f"Boats over 5 000: {[b.name.value for b in expensive]}")

# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

boat.price = Price(8_499.0)
with uow(repo):
    repo.update(boat)
    uow.commit()

with uow(repo):
    fetched = repo.get_one(boat.id)

print(f"Updated price:   {fetched.price.value}")

# ---------------------------------------------------------------------------
# Rollback on error
# ---------------------------------------------------------------------------

ghost = Boat(
    name=BoatName("Ghost"),
    price=Price(10.0),
    engine=Engine(name=EngineName("tiny"), price=Price(1.0)),
)

try:
    with uow(repo):
        repo.save(ghost)
        raise RuntimeError("something went wrong")
except RuntimeError:
    pass

with uow(repo):
    try:
        repo.get_one(ghost.id)
        print("ERROR: ghost should not exist")
    except AggregateDoesNotExistError:
        print("Rollback confirmed: ghost was not persisted")
