"""SQLAlchemy provider example.

Demonstrates save, query, update, and rollback using PostgreSQL as the
backing store via ``SQLAlchemyDBContext`` and ``SQLAlchemyRepository``
with the auto-inferring ``DictAutoSQLAlchemyMapper``.

The mapper derives the full relational schema from aggregate annotations:
one table per entity class, no hand-written ORM models required.

Start with devenv::

    devenv up postgres

Run with::

    uv run python examples/providers/sqlalchemy_provider.py
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

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
from hike.persistence.providers.sqlalchemy import (
    DictAutoSQLAlchemyMapper,
    SQLAlchemyDBContext,
    SQLAlchemyRepository,
)


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

engine = create_engine("postgresql+psycopg://localhost:5432/hike", echo=False)

mapper = DictAutoSQLAlchemyMapper(Boat)
mapper.create_tables(engine)           # no-op if tables already exist

# Drop and recreate for a repeatable demo
mapper._base.metadata.drop_all(engine)  # pyright: ignore[reportPrivateUsage]
mapper.create_tables(engine)

session_factory: sessionmaker[Session] = sessionmaker(bind=engine)
ctx = SQLAlchemyDBContext(session_factory)
repo: SQLAlchemyRepository[UUID, Boat] = SQLAlchemyRepository(Boat, mapper)
uow: UnitOfWork[Session] = UnitOfWork(ctx)

# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

eng = Engine(name=EngineName("V8 Turbo"), price=Price(1_500.0))
boat = Boat(name=BoatName("Sea Spirit"), price=Price(9_999.0), engine=eng)

with uow(repo):
    repo.save(boat)
    uow.commit()

print(f"Saved:   {boat.name.value!r} (id={boat.id.value})")

# ---------------------------------------------------------------------------
# Query with a specification
# ---------------------------------------------------------------------------

budget_eng = Engine(name=EngineName("40hp Outboard"), price=Price(400.0))
budget_boat = Boat(name=BoatName("Dinghy"), price=Price(799.0), engine=budget_eng)

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
