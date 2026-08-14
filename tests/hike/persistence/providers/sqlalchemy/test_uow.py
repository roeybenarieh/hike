"""Integration tests for UnitOfWork + SQLAlchemyRepository against a real PostgreSQL
instance managed by testcontainers.

Run with::

    uv run pytest tests/test_sqlalchemy_uow.py -v
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Float, ForeignKey, JSON, String, Uuid, create_engine, delete as sa_delete
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

from hike.entity import EntityID
from hike.persistence.pagination import CursorPagination, OffsetPagination, Page, PagePagination
from hike.persistence.providers.sqlalchemy import (
    DictAutoSQLAlchemyMapper,
    DictSQLAlchemyMapper,
    FlatSQLAlchemyMapper,
    SQLAlchemyDBContext,
    SQLAlchemyRepository,
)
from hike.persistence.repository import AggregateAlreadyExistError, AggregateDoesNotExistError, OptimisticLockError, get_version
from hike.persistence.uow import UnitOfWork

from tests.hike.conftest import Boat, BoatEngine, Checkpoint, Journey, MotorBoat, Name, Price


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


# ---------------------------------------------------------------------------
# SQLAlchemy ORM models
# ---------------------------------------------------------------------------


class Base(DeclarativeBase): ...


class BoatModel(Base):
    __tablename__ = "boats"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)


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


class JourneyModel(Base):
    __tablename__ = "journeys"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    checkpoints: Mapped[list[CheckpointModel]] = relationship(
        "CheckpointModel", cascade="all, delete-orphan", lazy="joined"
    )


class CheckpointModel(Base):
    __tablename__ = "checkpoints"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    journey_id: Mapped[UUID] = mapped_column(ForeignKey("journeys.id"), nullable=False)


class FlatMotorBoatModel(Base):
    __tablename__ = "flat_motorboats"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    engine_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    engine_name: Mapped[str] = mapped_column(String, nullable=False)
    engine_price: Mapped[float] = mapped_column(Float, nullable=False)


class FlatJourneyModel(Base):
    __tablename__ = "flat_journeys"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    checkpoints: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)


# ---------------------------------------------------------------------------
# DictAutoSQLAlchemyMapper — module-level instances (schema inferred from annotations)
# ---------------------------------------------------------------------------


class AutoBase(DeclarativeBase): ...


# Each mapper registers its generated model classes in AutoBase.metadata.
# Boat/Journey/Checkpoint share table names with Base (handled by checkfirst=True);
# MotorBoat → "motor_boats" and BoatEngine → "boat_engines" are new tables.
auto_boat_mapper = DictAutoSQLAlchemyMapper(Boat, base=AutoBase)
auto_motorboat_mapper = DictAutoSQLAlchemyMapper(MotorBoat, base=AutoBase)
auto_journey_mapper = DictAutoSQLAlchemyMapper(Journey, base=AutoBase)

# ---------------------------------------------------------------------------
# DictSQLAlchemyMapper / FlatSQLAlchemyMapper — module-level instances
#
# Must be created before Base.metadata.create_all() so that the version column
# injected by the mapper constructors is included in the CREATE TABLE statements.
# ---------------------------------------------------------------------------

dict_boat_mapper = DictSQLAlchemyMapper({Boat: BoatModel})
dict_motorboat_mapper = DictSQLAlchemyMapper({MotorBoat: MotorBoatModel, BoatEngine: EngineModel})
dict_journey_mapper = DictSQLAlchemyMapper({Journey: JourneyModel, Checkpoint: CheckpointModel})
flat_motorboat_mapper = FlatSQLAlchemyMapper(FlatMotorBoatModel)
flat_journey_mapper = FlatSQLAlchemyMapper(FlatJourneyModel)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_engine() -> SAEngine:  # type: ignore[misc]
    with PostgresContainer("postgres:16", driver="psycopg") as pg:
        engine = create_engine(
            pg.get_connection_url(),
            json_serializer=_json_dumps,
        )
        Base.metadata.create_all(engine)
        # checkfirst=True skips tables already created by Base (boats, journeys, checkpoints);
        # creates only the new auto-mapper tables (motor_boats, boat_engines).
        AutoBase.metadata.create_all(engine, checkfirst=True)
        yield engine  # type: ignore[misc]
        Base.metadata.drop_all(engine)
        AutoBase.metadata.drop_all(engine, checkfirst=True)


@pytest.fixture(autouse=True)
def truncate_tables(pg_engine: SAEngine) -> None:  # type: ignore[misc]
    """Wipe all tables after each test for isolation."""
    yield  # type: ignore[misc]
    with pg_engine.connect() as conn:
        # Delete child tables before parent (FK constraints).
        # Auto motor_boats/boat_engines are separate tables; auto boats/journeys/checkpoints
        # share tables with the dict mapper and are covered by the existing deletes.
        conn.execute(sa_delete(auto_motorboat_mapper.get_model(MotorBoat)))  # motor_boats
        conn.execute(sa_delete(auto_motorboat_mapper.get_model(BoatEngine)))  # boat_engines
        conn.execute(sa_delete(CheckpointModel))
        conn.execute(sa_delete(MotorBoatModel))
        conn.execute(sa_delete(JourneyModel))
        conn.execute(sa_delete(BoatModel))
        conn.execute(sa_delete(EngineModel))
        conn.execute(sa_delete(FlatMotorBoatModel))
        conn.execute(sa_delete(FlatJourneyModel))
        conn.commit()


@pytest.fixture
def uow(pg_engine: SAEngine) -> UnitOfWork[Session, UUID, Boat]:
    factory = sessionmaker(pg_engine)
    ctx = SQLAlchemyDBContext(factory)
    repo: SQLAlchemyRepository[UUID, Boat] = SQLAlchemyRepository(Boat, dict_boat_mapper)
    return UnitOfWork(ctx, repo=repo)


@pytest.fixture
def motorboat_uow(pg_engine: SAEngine) -> UnitOfWork[Session, UUID, MotorBoat]:
    factory = sessionmaker(pg_engine)
    ctx = SQLAlchemyDBContext(factory)
    repo: SQLAlchemyRepository[UUID, MotorBoat] = SQLAlchemyRepository(MotorBoat, dict_motorboat_mapper)
    return UnitOfWork(ctx, repo=repo)


@pytest.fixture
def journey_uow(pg_engine: SAEngine) -> UnitOfWork[Session, UUID, Journey]:
    factory = sessionmaker(pg_engine)
    ctx = SQLAlchemyDBContext(factory)
    repo: SQLAlchemyRepository[UUID, Journey] = SQLAlchemyRepository(Journey, dict_journey_mapper)
    return UnitOfWork(ctx, repo=repo)


@pytest.fixture
def flat_motorboat_uow(pg_engine: SAEngine) -> UnitOfWork[Session, UUID, MotorBoat]:
    factory = sessionmaker(pg_engine)
    ctx = SQLAlchemyDBContext(factory)
    repo: SQLAlchemyRepository[UUID, MotorBoat] = SQLAlchemyRepository(MotorBoat, flat_motorboat_mapper)
    return UnitOfWork(ctx, repo=repo)


@pytest.fixture
def flat_journey_uow(pg_engine: SAEngine) -> UnitOfWork[Session, UUID, Journey]:
    factory = sessionmaker(pg_engine)
    ctx = SQLAlchemyDBContext(factory)
    repo: SQLAlchemyRepository[UUID, Journey] = SQLAlchemyRepository(Journey, flat_journey_mapper)
    return UnitOfWork(ctx, repo=repo)


@pytest.fixture
def auto_uow(pg_engine: SAEngine) -> UnitOfWork[Session, UUID, Boat]:
    factory = sessionmaker(pg_engine)
    ctx = SQLAlchemyDBContext(factory)
    repo: SQLAlchemyRepository[UUID, Boat] = SQLAlchemyRepository(Boat, auto_boat_mapper)
    return UnitOfWork(ctx, repo=repo)


@pytest.fixture
def auto_motorboat_uow(pg_engine: SAEngine) -> UnitOfWork[Session, UUID, MotorBoat]:
    factory = sessionmaker(pg_engine)
    ctx = SQLAlchemyDBContext(factory)
    repo: SQLAlchemyRepository[UUID, MotorBoat] = SQLAlchemyRepository(MotorBoat, auto_motorboat_mapper)
    return UnitOfWork(ctx, repo=repo)


@pytest.fixture
def auto_journey_uow(pg_engine: SAEngine) -> UnitOfWork[Session, UUID, Journey]:
    factory = sessionmaker(pg_engine)
    ctx = SQLAlchemyDBContext(factory)
    repo: SQLAlchemyRepository[UUID, Journey] = SQLAlchemyRepository(Journey, auto_journey_mapper)
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


def test_count_with_spec(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    boat_a = Boat(name=Name("Alpha"), price=Price(10.0))
    boat_b = Boat(name=Name("Beta"), price=Price(50.0))
    boat_c = Boat(name=Name("Gamma"), price=Price(80.0))

    with uow:
        uow.repo.save(boat_a)
        uow.repo.save(boat_b)
        uow.repo.save(boat_c)
        uow.commit()

    with uow:
        assert uow.repo.count(Boat.price > 20.0) == 2

    with uow:
        assert uow.repo.count(Boat.price > 100.0) == 0


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

    assert get_version(copy_a) == 0
    assert get_version(copy_b) == 0

    copy_a.price = Price(200.0)
    with uow:
        uow.repo.update(copy_a)
        uow.commit()
    assert get_version(copy_a) == 1

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
    boat.update_engine_price(Price(3_000.0))
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


# ---------------------------------------------------------------------------
# Tests — MotorBoat with FlatSQLAlchemyMapper (single-table, flattened columns)
# ---------------------------------------------------------------------------


def test_flat_motorboat_save_and_get_one(flat_motorboat_uow: UnitOfWork[Session, UUID, MotorBoat]) -> None:
    boat = make_motorboat("Sea Spirit", boat_price=4_999.99, engine_price=1_200.0)

    with flat_motorboat_uow:
        flat_motorboat_uow.repo.save(boat)
        flat_motorboat_uow.commit()

    with flat_motorboat_uow:
        fetched = flat_motorboat_uow.repo.get_one(boat.id)

    assert fetched.name == Name("Sea Spirit")
    assert fetched.price == Price(4_999.99)
    assert fetched.engine.price == Price(1_200.0)


def test_flat_motorboat_filter_by_engine_price(flat_motorboat_uow: UnitOfWork[Session, UUID, MotorBoat]) -> None:
    expensive = make_motorboat("Yacht", boat_price=50_000.0, engine_price=5_000.0)
    cheap = make_motorboat("Dinghy", boat_price=500.0, engine_price=200.0)

    with flat_motorboat_uow:
        flat_motorboat_uow.repo.save(expensive)
        flat_motorboat_uow.repo.save(cheap)
        flat_motorboat_uow.commit()

    with flat_motorboat_uow:
        results = flat_motorboat_uow.repo.get_many(MotorBoat.engine.price > 1_000.0)

    assert len(results) == 1
    assert results[0].name == Name("Yacht")


def test_flat_motorboat_update_engine_price(flat_motorboat_uow: UnitOfWork[Session, UUID, MotorBoat]) -> None:
    boat = make_motorboat("Cruiser", boat_price=10_000.0, engine_price=800.0)

    with flat_motorboat_uow:
        flat_motorboat_uow.repo.save(boat)
        flat_motorboat_uow.commit()

    boat.update_engine_price(Price(3_000.0))
    with flat_motorboat_uow:
        flat_motorboat_uow.repo.update(boat)
        flat_motorboat_uow.commit()

    with flat_motorboat_uow:
        fetched = flat_motorboat_uow.repo.get_one(boat.id)

    assert fetched.engine.price == Price(3_000.0)


# ---------------------------------------------------------------------------
# Tests — Journey with FlatSQLAlchemyMapper (single-table, JSON list column)
# ---------------------------------------------------------------------------


def test_flat_journey_save_and_get_one(flat_journey_uow: UnitOfWork[Session, UUID, Journey]) -> None:
    cp1 = Checkpoint(name=Name("Paris"))
    cp2 = Checkpoint(name=Name("Lyon"))
    journey = Journey(name=Name("France Trip"), checkpoints=[cp1, cp2])

    with flat_journey_uow:
        flat_journey_uow.repo.save(journey)
        flat_journey_uow.commit()

    with flat_journey_uow:
        fetched = flat_journey_uow.repo.get_one(journey.id)

    assert fetched.name == Name("France Trip")
    assert len(fetched.checkpoints) == 2
    assert {cp.name for cp in fetched.checkpoints} == {Name("Paris"), Name("Lyon")}


def test_flat_journey_update_checkpoints(flat_journey_uow: UnitOfWork[Session, UUID, Journey]) -> None:
    journey = Journey(name=Name("Tour"), checkpoints=[Checkpoint(name=Name("A"))])

    with flat_journey_uow:
        flat_journey_uow.repo.save(journey)
        flat_journey_uow.commit()

    journey.checkpoints.append(Checkpoint(name=Name("B")))
    with flat_journey_uow:
        flat_journey_uow.repo.update(journey)
        flat_journey_uow.commit()

    with flat_journey_uow:
        fetched = flat_journey_uow.repo.get_one(journey.id)

    assert len(fetched.checkpoints) == 2


# ---------------------------------------------------------------------------
# Tests — DictAutoSQLAlchemyMapper (schema inferred from annotations)
# ---------------------------------------------------------------------------


def test_auto_save_and_get_one(auto_uow: UnitOfWork[Session, UUID, Boat]) -> None:
    boat = Boat(name=Name("Sea Spirit"), price=Price(4_999.99))

    with auto_uow:
        auto_uow.repo.save(boat)
        auto_uow.commit()

    with auto_uow:
        fetched = auto_uow.repo.get_one(boat.id)

    assert fetched.name == Name("Sea Spirit")
    assert fetched.price == Price(4_999.99)


def test_auto_update(auto_uow: UnitOfWork[Session, UUID, Boat]) -> None:
    boat = Boat(name=Name("Old Name"), price=Price(100.0))

    with auto_uow:
        auto_uow.repo.save(boat)
        auto_uow.commit()

    boat.price = Price(200.0)
    with auto_uow:
        auto_uow.repo.update(boat)
        auto_uow.commit()

    with auto_uow:
        fetched = auto_uow.repo.get_one(boat.id)

    assert fetched.price == Price(200.0)


def test_auto_get_many_with_spec(auto_uow: UnitOfWork[Session, UUID, Boat]) -> None:
    boat_a = Boat(name=Name("Alpha"), price=Price(10.0))
    boat_b = Boat(name=Name("Beta"), price=Price(50.0))

    with auto_uow:
        auto_uow.repo.save(boat_a)
        auto_uow.repo.save(boat_b)
        auto_uow.commit()

    with auto_uow:
        results = auto_uow.repo.get_many(Boat.price > 20.0)

    assert len(results) == 1
    assert results[0].name == Name("Beta")


def test_auto_count_with_spec(auto_uow: UnitOfWork[Session, UUID, Boat]) -> None:
    boat_a = Boat(name=Name("Alpha"), price=Price(10.0))
    boat_b = Boat(name=Name("Beta"), price=Price(50.0))
    boat_c = Boat(name=Name("Gamma"), price=Price(80.0))

    with auto_uow:
        auto_uow.repo.save(boat_a)
        auto_uow.repo.save(boat_b)
        auto_uow.repo.save(boat_c)
        auto_uow.commit()

    with auto_uow:
        assert auto_uow.repo.count(Boat.price > 20.0) == 2

    with auto_uow:
        assert auto_uow.repo.count(Boat.price > 100.0) == 0


def test_auto_optimistic_lock_conflict(auto_uow: UnitOfWork[Session, UUID, Boat]) -> None:
    boat = Boat(name=Name("Contested"), price=Price(100.0))

    with auto_uow:
        auto_uow.repo.save(boat)
        auto_uow.commit()

    with auto_uow:
        copy_a = auto_uow.repo.get_one(boat.id)
    with auto_uow:
        copy_b = auto_uow.repo.get_one(boat.id)

    copy_a.price = Price(200.0)
    with auto_uow:
        auto_uow.repo.update(copy_a)
        auto_uow.commit()

    copy_b.price = Price(300.0)
    with pytest.raises(OptimisticLockError):
        with auto_uow:
            auto_uow.repo.update(copy_b)
            auto_uow.commit()


def test_auto_motorboat_save_and_get_one(auto_motorboat_uow: UnitOfWork[Session, UUID, MotorBoat]) -> None:
    boat = make_motorboat("Sea Spirit", boat_price=4_999.99, engine_price=1_200.0)

    with auto_motorboat_uow:
        auto_motorboat_uow.repo.save(boat)
        auto_motorboat_uow.commit()

    with auto_motorboat_uow:
        fetched = auto_motorboat_uow.repo.get_one(boat.id)

    assert fetched.name == Name("Sea Spirit")
    assert fetched.price == Price(4_999.99)
    assert fetched.engine.price == Price(1_200.0)


def test_auto_motorboat_filter_by_engine_price(auto_motorboat_uow: UnitOfWork[Session, UUID, MotorBoat]) -> None:
    expensive = make_motorboat("Yacht", boat_price=50_000.0, engine_price=5_000.0)
    cheap = make_motorboat("Dinghy", boat_price=500.0, engine_price=200.0)

    with auto_motorboat_uow:
        auto_motorboat_uow.repo.save(expensive)
        auto_motorboat_uow.repo.save(cheap)
        auto_motorboat_uow.commit()

    with auto_motorboat_uow:
        results = auto_motorboat_uow.repo.get_many(MotorBoat.engine.price > 1_000.0)

    assert len(results) == 1
    assert results[0].name == Name("Yacht")


def test_auto_motorboat_update_engine_price(auto_motorboat_uow: UnitOfWork[Session, UUID, MotorBoat]) -> None:
    boat = make_motorboat("Cruiser", boat_price=10_000.0, engine_price=800.0)

    with auto_motorboat_uow:
        auto_motorboat_uow.repo.save(boat)
        auto_motorboat_uow.commit()

    boat.update_engine_price(Price(3_000.0))
    with auto_motorboat_uow:
        auto_motorboat_uow.repo.update(boat)
        auto_motorboat_uow.commit()

    with auto_motorboat_uow:
        fetched = auto_motorboat_uow.repo.get_one(boat.id)

    assert fetched.engine.price == Price(3_000.0)


def test_auto_journey_save_and_get_one_with_checkpoints(
    auto_journey_uow: UnitOfWork[Session, UUID, Journey],
) -> None:
    cp1 = Checkpoint(name=Name("Paris"))
    cp2 = Checkpoint(name=Name("Lyon"))
    journey = Journey(name=Name("France Trip"), checkpoints=[cp1, cp2])

    with auto_journey_uow:
        auto_journey_uow.repo.save(journey)
        auto_journey_uow.commit()

    with auto_journey_uow:
        fetched = auto_journey_uow.repo.get_one(journey.id)

    assert fetched.name == Name("France Trip")
    assert len(fetched.checkpoints) == 2
    assert {cp.name for cp in fetched.checkpoints} == {Name("Paris"), Name("Lyon")}


def test_auto_journey_update_checkpoints(auto_journey_uow: UnitOfWork[Session, UUID, Journey]) -> None:
    journey = Journey(name=Name("Tour"), checkpoints=[Checkpoint(name=Name("A"))])

    with auto_journey_uow:
        auto_journey_uow.repo.save(journey)
        auto_journey_uow.commit()

    journey.checkpoints.append(Checkpoint(name=Name("B")))
    with auto_journey_uow:
        auto_journey_uow.repo.update(journey)
        auto_journey_uow.commit()

    with auto_journey_uow:
        fetched = auto_journey_uow.repo.get_one(journey.id)

    assert len(fetched.checkpoints) == 2


# ---------------------------------------------------------------------------
# Pagination and ordering tests (dict_boat_mapper / pg_engine)
# ---------------------------------------------------------------------------


@pytest.fixture
def fleet(uow: UnitOfWork[Session, UUID, Boat]) -> list[Boat]:
    """Save five boats with prices 10–50 and names A–E; return them price-sorted."""
    boats = [
        Boat(name=Name(n), price=Price(p))
        for n, p in zip("ABCDE", [10.0, 20.0, 30.0, 40.0, 50.0])
    ]
    with uow:
        for b in boats:
            uow.repo.save(b)
        uow.commit()
    return boats


@pytest.mark.usefixtures("fleet")
def test_sa_ordering_price_asc(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    with uow:
        results = uow.repo.get_many(Boat.price >= 0.0, ordering=[Boat.price.asc()])
    prices = [b.price.value for b in results]
    assert prices == sorted(prices)


@pytest.mark.usefixtures("fleet")
def test_sa_ordering_price_desc(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    with uow:
        results = uow.repo.get_many(Boat.price >= 0.0, ordering=[Boat.price.desc()])
    prices = [b.price.value for b in results]
    assert prices == sorted(prices, reverse=True)


@pytest.mark.usefixtures("fleet")
def test_sa_offset_pagination_first_page(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    with uow:
        page = uow.repo.get_many(
            Boat.price >= 0.0,
            ordering=[Boat.price.asc()],
            pagination=OffsetPagination(offset=0, limit=2),
        )
    assert isinstance(page, Page)
    assert page.total == 5
    assert page.has_next is True
    assert [b.price.value for b in page.items] == [10.0, 20.0]


@pytest.mark.usefixtures("fleet")
def test_sa_offset_pagination_last_page(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    with uow:
        page = uow.repo.get_many(
            Boat.price >= 0.0,
            ordering=[Boat.price.asc()],
            pagination=OffsetPagination(offset=4, limit=2),
        )
    assert isinstance(page, Page)
    assert page.has_next is False
    assert [b.price.value for b in page.items] == [50.0]


@pytest.mark.usefixtures("fleet")
def test_sa_page_pagination(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    with uow:
        page = uow.repo.get_many(
            Boat.price >= 0.0,
            ordering=[Boat.price.asc()],
            pagination=PagePagination(page=2, page_size=2),
        )
    assert isinstance(page, Page)
    assert page.total == 5
    assert [b.price.value for b in page.items] == [30.0, 40.0]


@pytest.mark.usefixtures("fleet")
def test_sa_cursor_pagination_traverses_all(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    collected: list[float] = []
    cursor: str | None = None

    for _ in range(10):
        with uow:
            page = uow.repo.get_many(
                Boat.price >= 0.0,
                ordering=[Boat.price.asc()],
                pagination=CursorPagination(limit=2, cursor=cursor),
            )
        assert isinstance(page, Page)
        collected.extend(b.price.value for b in page.items)
        if not page.has_next:
            break
        cursor = page.next_cursor
    else:
        pytest.fail("Cursor pagination did not terminate")

    assert collected == [10.0, 20.0, 30.0, 40.0, 50.0]


@pytest.mark.usefixtures("fleet")
def test_sa_spec_with_offset_pagination(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    with uow:
        page = uow.repo.get_many(
            Boat.price > 20.0,
            ordering=[Boat.price.asc()],
            pagination=OffsetPagination(offset=0, limit=2),
        )
    assert isinstance(page, Page)
    assert page.total == 3
    assert [b.price.value for b in page.items] == [30.0, 40.0]


@pytest.mark.usefixtures("fleet")
def test_sa_ordering_single_orderby_shorthand(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    with uow:
        results = uow.repo.get_many(Boat.price >= 0.0, ordering=Boat.price.asc())
    assert isinstance(results, list)
    prices = [b.price.value for b in results]
    assert prices == sorted(prices)


@pytest.mark.usefixtures("fleet")
def test_sa_get_many_no_pagination_returns_list(uow: UnitOfWork[Session, UUID, Boat]) -> None:
    with uow:
        results = uow.repo.get_many(Boat.price >= 0.0)
    assert isinstance(results, list)
    assert len(results) == 5
