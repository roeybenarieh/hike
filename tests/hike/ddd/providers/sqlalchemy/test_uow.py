"""Integration tests for UnitOfWork + SQLAlchemyRepository against a real PostgreSQL
instance managed by testcontainers.

Run with::

    uv run pytest tests/test_sqlalchemy_uow.py -v
"""
from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import Float, ForeignKey, Integer, String, Uuid, create_engine, delete as sa_delete
from sqlalchemy.engine import Engine as SAEngine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)
from testcontainers.community.postgres import PostgresContainer  # pyright: ignore[reportMissingImports]

from hike.ddd.entity import EntityID
from hike.ddd.providers.sqlalchemy import DictSQLAlchemyMapper, SQLAlchemyDBContext, SQLAlchemyRepository
from hike.ddd.repository import AggregateAlreadyExistError, AggregateDoesNotExistError, OptimisticLockError
from hike.ddd.uow import UnitOfWork

from tests.hike.ddd.conftest import Boat, BoatEngine, Checkpoint, Journey, MotorBoat, Name, Price


# ---------------------------------------------------------------------------
# SQLAlchemy ORM models
# ---------------------------------------------------------------------------


class Base(DeclarativeBase): ...


class BoatModel(Base):
    __tablename__ = "boats"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class EngineModel(Base):
    __tablename__ = "engines"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)


class MotorBoatModel(Base):
    __tablename__ = "motorboats"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    engine_id: Mapped[UUID] = mapped_column(ForeignKey("engines.id"), nullable=False)
    engine: Mapped[EngineModel] = relationship(EngineModel)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class JourneyModel(Base):
    __tablename__ = "journeys"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checkpoints: Mapped[list[CheckpointModel]] = relationship(
        "CheckpointModel", cascade="all, delete-orphan", lazy="joined"
    )


class CheckpointModel(Base):
    __tablename__ = "checkpoints"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    journey_id: Mapped[UUID] = mapped_column(ForeignKey("journeys.id"), nullable=False)




# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_engine() -> SAEngine:  # type: ignore[misc]
    with PostgresContainer("postgres:16", driver="psycopg") as pg:
        engine = create_engine(pg.get_connection_url())
        Base.metadata.create_all(engine)
        yield engine  # type: ignore[misc]
        Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def truncate_tables(pg_engine: SAEngine) -> None:  # type: ignore[misc]
    """Wipe all tables after each test for isolation."""
    yield  # type: ignore[misc]
    with pg_engine.connect() as conn:
        # Delete child tables before parent (FK constraints)
        conn.execute(sa_delete(CheckpointModel))
        conn.execute(sa_delete(MotorBoatModel))
        conn.execute(sa_delete(JourneyModel))
        conn.execute(sa_delete(BoatModel))
        conn.execute(sa_delete(EngineModel))
        conn.commit()


@pytest.fixture
def uow(pg_engine: SAEngine) -> UnitOfWork[Session, UUID, Boat]:
    factory = sessionmaker(pg_engine)
    ctx = SQLAlchemyDBContext(factory)
    repo: SQLAlchemyRepository[UUID, Boat] = SQLAlchemyRepository(
        Boat, DictSQLAlchemyMapper({Boat: BoatModel})
    )
    return UnitOfWork(ctx, repo=repo)


@pytest.fixture
def motorboat_uow(pg_engine: SAEngine) -> UnitOfWork[Session, UUID, MotorBoat]:
    factory = sessionmaker(pg_engine)
    ctx = SQLAlchemyDBContext(factory)
    repo: SQLAlchemyRepository[UUID, MotorBoat] = SQLAlchemyRepository(
        MotorBoat, DictSQLAlchemyMapper({MotorBoat: MotorBoatModel, BoatEngine: EngineModel})
    )
    return UnitOfWork(ctx, repo=repo)


@pytest.fixture
def journey_uow(pg_engine: SAEngine) -> UnitOfWork[Session, UUID, Journey]:
    factory = sessionmaker(pg_engine)
    ctx = SQLAlchemyDBContext(factory)
    repo: SQLAlchemyRepository[UUID, Journey] = SQLAlchemyRepository(
        Journey, DictSQLAlchemyMapper({Journey: JourneyModel, Checkpoint: CheckpointModel})
    )
    return UnitOfWork(ctx, repo=repo)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def make_motorboat(name: str, boat_price: float, engine_price: float) -> MotorBoat:
    engine = BoatEngine(name=Name("Engine"), price=Price(engine_price))
    return MotorBoat(name=Name(name), price=Price(boat_price), engine=engine)


# ---------------------------------------------------------------------------
# Tests — flat Boat aggregate (unchanged)
# ---------------------------------------------------------------------------


def test_save_and_get_one(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    boat = Boat(name=Name("Sea Spirit"), price=Price(4_999.99))

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        fetched = uow.repo.get_one(boat.id)

    assert fetched.name == Name("Sea Spirit")
    assert fetched.price == Price(4_999.99)


def test_update(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    boat = Boat(name=Name("Old Name"), price=Price(100.0))

    with uow:
        uow.repo.save(boat)
        uow.commit()

    boat.price = Price(200.0)
    with uow:
        uow.repo.update(boat)
        uow.commit()

    with uow:
        fetched = uow.repo.get_one(boat.id)

    assert fetched.price == Price(200.0)


def test_get_many_with_spec(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    boat_a = Boat(name=Name("Alpha"), price=Price(10.0))
    boat_b = Boat(name=Name("Beta"), price=Price(50.0))

    with uow:
        uow.repo.save(boat_a)
        uow.repo.save(boat_b)
        uow.commit()

    with uow:
        results = uow.repo.get_many(Boat.price > 20.0)

    assert len(results) == 1
    assert results[0].name == Name("Beta")


def test_upsert_creates_then_updates(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    boat = Boat(name=Name("Ghost"), price=Price(1.0))

    with uow:
        uow.repo.upsert(boat)
        uow.commit()

    boat.price = Price(2.0)
    with uow:
        uow.repo.upsert(boat)
        uow.commit()

    with uow:
        fetched = uow.repo.get_one(boat.id)

    assert fetched.price == Price(2.0)


def test_delete(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    boat = Boat(name=Name("Doomed"), price=Price(0.01))

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        uow.repo.delete(boat)
        uow.commit()

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(boat.id)


def test_save_duplicate_raises(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    boat = Boat(name=Name("Twin"), price=Price(50.0))

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with pytest.raises(AggregateAlreadyExistError):
        with uow:
            uow.repo.save(boat)
            uow.commit()


def test_rollback_on_exception(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    boat = Boat(name=Name("Rollback Boat"), price=Price(99.0))

    with pytest.raises(ValueError, match="simulated failure"):
        with uow:
            uow.repo.save(boat)
            raise ValueError("simulated failure")

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(boat.id)


def test_optimistic_lock_conflict(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    """Second writer loses when it holds a stale version."""
    boat = Boat(name=Name("Contested"), price=Price(100.0))

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        copy_a = uow.repo.get_one(boat.id)
    with uow:
        copy_b = uow.repo.get_one(boat.id)

    assert copy_a.version == 0
    assert copy_b.version == 0

    copy_a.price = Price(200.0)
    with uow:
        uow.repo.update(copy_a)
        uow.commit()
    assert copy_a.version == 1

    copy_b.price = Price(300.0)
    with pytest.raises(OptimisticLockError):
        with uow:
            uow.repo.update(copy_b)
            uow.commit()


def test_get_one_missing_raises(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    from uuid import uuid4

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(EntityID(uuid4()))


def test_delete_missing_raises(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    ghost = Boat(name=Name("Never Saved"), price=Price(1.0))

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.delete(ghost)


# ---------------------------------------------------------------------------
# Tests — MotorBoat with nested BoatEngine entity
# ---------------------------------------------------------------------------


def test_motorboat_save_and_get_one(motorboat_uow: UnitOfWork[Session, UUID, MotorBoat]) -> None:
    boat = make_motorboat("Sea Spirit", boat_price=4_999.99, engine_price=1_200.0)

    with motorboat_uow:
        motorboat_uow.repo.save(boat)
        motorboat_uow.commit()

    with motorboat_uow:
        fetched = motorboat_uow.repo.get_one(boat.id)

    assert fetched.name == Name("Sea Spirit")
    assert fetched.price == Price(4_999.99)
    assert fetched.engine.price == Price(1_200.0)


def test_motorboat_filter_by_engine_price(motorboat_uow: UnitOfWork[Session, UUID, MotorBoat]) -> None:
    """MotorBoat.engine.price > X produces a JOIN query and returns correct boats."""
    expensive = make_motorboat("Yacht", boat_price=50_000.0, engine_price=5_000.0)
    cheap = make_motorboat("Dinghy", boat_price=500.0, engine_price=200.0)

    with motorboat_uow:
        motorboat_uow.repo.save(expensive)
        motorboat_uow.repo.save(cheap)
        motorboat_uow.commit()

    with motorboat_uow:
        results = motorboat_uow.repo.get_many(MotorBoat.engine.price > 1_000.0)

    assert len(results) == 1
    assert results[0].name == Name("Yacht")


def test_motorboat_combined_spec(motorboat_uow: UnitOfWork[Session, UUID, MotorBoat]) -> None:
    """Spec combining boat price and engine price produces correct SQL with one JOIN."""
    a = make_motorboat("A", boat_price=1_000.0, engine_price=500.0)
    b = make_motorboat("B", boat_price=5_000.0, engine_price=500.0)
    c = make_motorboat("C", boat_price=5_000.0, engine_price=2_000.0)

    with motorboat_uow:
        motorboat_uow.repo.save(a)
        motorboat_uow.repo.save(b)
        motorboat_uow.repo.save(c)
        motorboat_uow.commit()

    # boat price > 2000 AND engine price > 1000 → only C
    spec = (MotorBoat.price > 2_000.0) & (MotorBoat.engine.price > 1_000.0)
    with motorboat_uow:
        results = motorboat_uow.repo.get_many(spec)

    assert len(results) == 1
    assert results[0].name == Name("C")


def test_motorboat_update_engine_price(motorboat_uow: UnitOfWork[Session, UUID, MotorBoat]) -> None:
    boat = make_motorboat("Cruiser", boat_price=10_000.0, engine_price=800.0)

    with motorboat_uow:
        motorboat_uow.repo.save(boat)
        motorboat_uow.commit()

    # Swap to a pricier engine
    boat.engine.price = Price(3_000.0)
    with motorboat_uow:
        motorboat_uow.repo.update(boat)
        motorboat_uow.commit()

    with motorboat_uow:
        fetched = motorboat_uow.repo.get_one(boat.id)

    assert fetched.engine.price == Price(3_000.0)


# ---------------------------------------------------------------------------
# Tests — Journey with embedded Checkpoint entities
# ---------------------------------------------------------------------------


def test_journey_save_and_get_one_with_checkpoints(journey_uow: UnitOfWork[Session, UUID, Journey]) -> None:
    cp1 = Checkpoint(name=Name("Paris"))
    cp2 = Checkpoint(name=Name("Lyon"))
    journey = Journey(name=Name("France Trip"), checkpoints=[cp1, cp2])

    with journey_uow:
        journey_uow.repo.save(journey)
        journey_uow.commit()

    with journey_uow:
        fetched = journey_uow.repo.get_one(journey.id)

    assert fetched.name == Name("France Trip")
    assert len(fetched.checkpoints) == 2
    assert {cp.name for cp in fetched.checkpoints} == {Name("Paris"), Name("Lyon")}


def test_journey_update_checkpoints(journey_uow: UnitOfWork[Session, UUID, Journey]) -> None:
    journey = Journey(name=Name("Tour"), checkpoints=[Checkpoint(name=Name("A"))])

    with journey_uow:
        journey_uow.repo.save(journey)
        journey_uow.commit()

    journey.checkpoints.append(Checkpoint(name=Name("B")))
    with journey_uow:
        journey_uow.repo.update(journey)
        journey_uow.commit()

    with journey_uow:
        fetched = journey_uow.repo.get_one(journey.id)

    assert len(fetched.checkpoints) == 2
