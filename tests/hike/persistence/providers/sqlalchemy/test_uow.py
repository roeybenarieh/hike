"""Integration tests for UnitOfWork + SQLAlchemyRepository against a real PostgreSQL
instance managed by testcontainers.

Run with::

    uv run pytest tests/hike/persistence/providers/sqlalchemy/test_uow.py -v
"""
from __future__ import annotations

import json
import multiprocessing
from collections.abc import Callable, Iterator
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

from hike.persistence.providers.sqlalchemy import (
    DictAutoSQLAlchemyMapper,
    DictSQLAlchemyMapper,
    FlatSQLAlchemyMapper,
    SQLAlchemyDBContext,
    SQLAlchemyRepository,
)
from hike.persistence.repository import OptimisticLockError
from hike.persistence.uow import UnitOfWork

from tests.hike.conftest import Boat, BoatEngine, Checkpoint, Journey, MotorBoat, Name, Price
from tests.hike.persistence.providers._subprocess_helpers import sa_insert_boat
from tests.hike.persistence.providers.parity_suite import CrossProcessWatchParitySuite, RepositoryParitySuite

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
        conn.execute(sa_delete(auto_motorboat_mapper.get_model(MotorBoat)))
        conn.execute(sa_delete(auto_motorboat_mapper.get_model(BoatEngine)))
        conn.execute(sa_delete(CheckpointModel))
        conn.execute(sa_delete(MotorBoatModel))
        conn.execute(sa_delete(JourneyModel))
        conn.execute(sa_delete(BoatModel))
        conn.execute(sa_delete(EngineModel))
        conn.execute(sa_delete(FlatMotorBoatModel))
        conn.execute(sa_delete(FlatJourneyModel))
        conn.commit()


@pytest.fixture
def uow(pg_engine: SAEngine) -> UnitOfWork[Session]:
    factory = sessionmaker(pg_engine)
    ctx = SQLAlchemyDBContext(factory)
    return UnitOfWork(ctx)


@pytest.fixture
def repo(pg_engine: SAEngine) -> SQLAlchemyRepository[UUID, Boat]:  # noqa: ARG001
    return SQLAlchemyRepository(Boat, dict_boat_mapper)


@pytest.fixture
def motorboat_repo(pg_engine: SAEngine) -> SQLAlchemyRepository[UUID, MotorBoat]:  # noqa: ARG001
    return SQLAlchemyRepository(MotorBoat, dict_motorboat_mapper)


@pytest.fixture
def journey_repo(pg_engine: SAEngine) -> SQLAlchemyRepository[UUID, Journey]:  # noqa: ARG001
    return SQLAlchemyRepository(Journey, dict_journey_mapper)


@pytest.fixture
def flat_motorboat_repo(pg_engine: SAEngine) -> SQLAlchemyRepository[UUID, MotorBoat]:  # noqa: ARG001
    return SQLAlchemyRepository(MotorBoat, flat_motorboat_mapper)


@pytest.fixture
def flat_journey_repo(pg_engine: SAEngine) -> SQLAlchemyRepository[UUID, Journey]:  # noqa: ARG001
    return SQLAlchemyRepository(Journey, flat_journey_mapper)


@pytest.fixture
def auto_repo(pg_engine: SAEngine) -> SQLAlchemyRepository[UUID, Boat]:  # noqa: ARG001
    return SQLAlchemyRepository(Boat, auto_boat_mapper)


@pytest.fixture
def auto_motorboat_repo(pg_engine: SAEngine) -> SQLAlchemyRepository[UUID, MotorBoat]:  # noqa: ARG001
    return SQLAlchemyRepository(MotorBoat, auto_motorboat_mapper)


@pytest.fixture
def auto_journey_repo(pg_engine: SAEngine) -> SQLAlchemyRepository[UUID, Journey]:  # noqa: ARG001
    return SQLAlchemyRepository(Journey, auto_journey_mapper)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def make_motorboat(name: str, boat_price: float, engine_price: float) -> MotorBoat:
    engine = BoatEngine(name=Name("Engine"), price=Price(engine_price))
    return MotorBoat(name=Name(name), price=Price(boat_price), engine=engine)


# ---------------------------------------------------------------------------
# Parity tests — IRepository interface via dict_boat_mapper / dict_journey_mapper
# ---------------------------------------------------------------------------


class TestSQLAlchemyRepositoryParity(RepositoryParitySuite, CrossProcessWatchParitySuite):
    @pytest.fixture
    def uow(self, pg_engine: SAEngine) -> UnitOfWork[Session]:
        factory = sessionmaker(pg_engine)
        ctx = SQLAlchemyDBContext(factory)
        return UnitOfWork(ctx)

    @pytest.fixture
    def repo(self, pg_engine: SAEngine) -> SQLAlchemyRepository[UUID, Boat]:  # noqa: ARG002
        return SQLAlchemyRepository(Boat, dict_boat_mapper)

    @pytest.fixture
    def journey_repo(self, pg_engine: SAEngine) -> SQLAlchemyRepository[UUID, Journey]:  # noqa: ARG002
        return SQLAlchemyRepository(Journey, dict_journey_mapper)

    @pytest.fixture
    def watch_repo(self, pg_engine: SAEngine) -> Iterator[SQLAlchemyRepository[UUID, Boat]]:
        repo = SQLAlchemyRepository(Boat, dict_boat_mapper)
        sess = sessionmaker(pg_engine)()
        repo.session = sess
        yield repo
        sess.close()

    @pytest.fixture
    def cross_process_insert(self, pg_engine: SAEngine) -> Callable[[Boat], None]:
        sa_url = pg_engine.url.render_as_string(hide_password=False)

        def _insert(boat: Boat) -> None:
            ctx = multiprocessing.get_context("spawn")
            p = ctx.Process(
                target=sa_insert_boat,
                args=(sa_url, str(boat.name.value), boat.price.value),
            )
            p.start()
            p.join(timeout=10)
            assert p.exitcode == 0, f"cross-process insert failed with exit code {p.exitcode}"

        return _insert


# ---------------------------------------------------------------------------
# Tests — MotorBoat with nested BoatEngine entity (dict mapper)
# ---------------------------------------------------------------------------


def test_motorboat_save_and_get_one(uow: UnitOfWork[Session], motorboat_repo: SQLAlchemyRepository[UUID, MotorBoat]) -> None:
    boat = make_motorboat("Sea Spirit", boat_price=4_999.99, engine_price=1_200.0)

    with uow(motorboat_repo):
        motorboat_repo.save(boat)
        uow.commit()

    with uow(motorboat_repo):
        fetched = motorboat_repo.get_one(boat.id)

    assert fetched.name == Name("Sea Spirit")
    assert fetched.price == Price(4_999.99)
    assert fetched.engine.price == Price(1_200.0)


def test_motorboat_filter_by_engine_price(uow: UnitOfWork[Session], motorboat_repo: SQLAlchemyRepository[UUID, MotorBoat]) -> None:
    """MotorBoat.engine.price > X produces a JOIN query and returns correct boats."""
    expensive = make_motorboat("Yacht", boat_price=50_000.0, engine_price=5_000.0)
    cheap = make_motorboat("Dinghy", boat_price=500.0, engine_price=200.0)

    with uow(motorboat_repo):
        motorboat_repo.save(expensive)
        motorboat_repo.save(cheap)
        uow.commit()

    with uow(motorboat_repo):
        results = motorboat_repo.get_many(MotorBoat.engine.price > 1_000.0)

    assert len(results) == 1
    assert results[0].name == Name("Yacht")


def test_motorboat_combined_spec(uow: UnitOfWork[Session], motorboat_repo: SQLAlchemyRepository[UUID, MotorBoat]) -> None:
    """Spec combining boat price and engine price produces correct SQL with one JOIN."""
    a = make_motorboat("A", boat_price=1_000.0, engine_price=500.0)
    b = make_motorboat("B", boat_price=5_000.0, engine_price=500.0)
    c = make_motorboat("C", boat_price=5_000.0, engine_price=2_000.0)

    with uow(motorboat_repo):
        motorboat_repo.save(a)
        motorboat_repo.save(b)
        motorboat_repo.save(c)
        uow.commit()

    spec = (MotorBoat.price > 2_000.0) & (MotorBoat.engine.price > 1_000.0)
    with uow(motorboat_repo):
        results = motorboat_repo.get_many(spec)

    assert len(results) == 1
    assert results[0].name == Name("C")


def test_motorboat_update_engine_price(uow: UnitOfWork[Session], motorboat_repo: SQLAlchemyRepository[UUID, MotorBoat]) -> None:
    boat = make_motorboat("Cruiser", boat_price=10_000.0, engine_price=800.0)

    with uow(motorboat_repo):
        motorboat_repo.save(boat)
        uow.commit()

    boat.update_engine_price(Price(3_000.0))
    with uow(motorboat_repo):
        motorboat_repo.update(boat)
        uow.commit()

    with uow(motorboat_repo):
        fetched = motorboat_repo.get_one(boat.id)

    assert fetched.engine.price == Price(3_000.0)


# ---------------------------------------------------------------------------
# Tests — MotorBoat with FlatSQLAlchemyMapper (single-table, flattened columns)
# ---------------------------------------------------------------------------


def test_flat_motorboat_save_and_get_one(uow: UnitOfWork[Session], flat_motorboat_repo: SQLAlchemyRepository[UUID, MotorBoat]) -> None:
    boat = make_motorboat("Sea Spirit", boat_price=4_999.99, engine_price=1_200.0)

    with uow(flat_motorboat_repo):
        flat_motorboat_repo.save(boat)
        uow.commit()

    with uow(flat_motorboat_repo):
        fetched = flat_motorboat_repo.get_one(boat.id)

    assert fetched.name == Name("Sea Spirit")
    assert fetched.price == Price(4_999.99)
    assert fetched.engine.price == Price(1_200.0)


def test_flat_motorboat_filter_by_engine_price(uow: UnitOfWork[Session], flat_motorboat_repo: SQLAlchemyRepository[UUID, MotorBoat]) -> None:
    expensive = make_motorboat("Yacht", boat_price=50_000.0, engine_price=5_000.0)
    cheap = make_motorboat("Dinghy", boat_price=500.0, engine_price=200.0)

    with uow(flat_motorboat_repo):
        flat_motorboat_repo.save(expensive)
        flat_motorboat_repo.save(cheap)
        uow.commit()

    with uow(flat_motorboat_repo):
        results = flat_motorboat_repo.get_many(MotorBoat.engine.price > 1_000.0)

    assert len(results) == 1
    assert results[0].name == Name("Yacht")


def test_flat_motorboat_update_engine_price(uow: UnitOfWork[Session], flat_motorboat_repo: SQLAlchemyRepository[UUID, MotorBoat]) -> None:
    boat = make_motorboat("Cruiser", boat_price=10_000.0, engine_price=800.0)

    with uow(flat_motorboat_repo):
        flat_motorboat_repo.save(boat)
        uow.commit()

    boat.update_engine_price(Price(3_000.0))
    with uow(flat_motorboat_repo):
        flat_motorboat_repo.update(boat)
        uow.commit()

    with uow(flat_motorboat_repo):
        fetched = flat_motorboat_repo.get_one(boat.id)

    assert fetched.engine.price == Price(3_000.0)


# ---------------------------------------------------------------------------
# Tests — Journey with FlatSQLAlchemyMapper (single-table, JSON list column)
# ---------------------------------------------------------------------------


def test_flat_journey_save_and_get_one(uow: UnitOfWork[Session], flat_journey_repo: SQLAlchemyRepository[UUID, Journey]) -> None:
    cp1 = Checkpoint(name=Name("Paris"))
    cp2 = Checkpoint(name=Name("Lyon"))
    journey = Journey(name=Name("France Trip"), checkpoints=[cp1, cp2])

    with uow(flat_journey_repo):
        flat_journey_repo.save(journey)
        uow.commit()

    with uow(flat_journey_repo):
        fetched = flat_journey_repo.get_one(journey.id)

    assert fetched.name == Name("France Trip")
    assert len(fetched.checkpoints) == 2
    assert {cp.name for cp in fetched.checkpoints} == {Name("Paris"), Name("Lyon")}


def test_flat_journey_update_checkpoints(uow: UnitOfWork[Session], flat_journey_repo: SQLAlchemyRepository[UUID, Journey]) -> None:
    journey = Journey(name=Name("Tour"), checkpoints=[Checkpoint(name=Name("A"))])

    with uow(flat_journey_repo):
        flat_journey_repo.save(journey)
        uow.commit()

    journey.checkpoints.append(Checkpoint(name=Name("B")))
    with uow(flat_journey_repo):
        flat_journey_repo.update(journey)
        uow.commit()

    with uow(flat_journey_repo):
        fetched = flat_journey_repo.get_one(journey.id)

    assert len(fetched.checkpoints) == 2


# ---------------------------------------------------------------------------
# Tests — DictAutoSQLAlchemyMapper (schema inferred from annotations)
# ---------------------------------------------------------------------------


def test_auto_save_and_get_one(uow: UnitOfWork[Session], auto_repo: SQLAlchemyRepository[UUID, Boat]) -> None:
    boat = Boat(name=Name("Sea Spirit"), price=Price(4_999.99))

    with uow(auto_repo):
        auto_repo.save(boat)
        uow.commit()

    with uow(auto_repo):
        fetched = auto_repo.get_one(boat.id)

    assert fetched.name == Name("Sea Spirit")
    assert fetched.price == Price(4_999.99)


def test_auto_update(uow: UnitOfWork[Session], auto_repo: SQLAlchemyRepository[UUID, Boat]) -> None:
    boat = Boat(name=Name("Old Name"), price=Price(100.0))

    with uow(auto_repo):
        auto_repo.save(boat)
        uow.commit()

    boat.price = Price(200.0)
    with uow(auto_repo):
        auto_repo.update(boat)
        uow.commit()

    with uow(auto_repo):
        fetched = auto_repo.get_one(boat.id)

    assert fetched.price == Price(200.0)


def test_auto_get_many_with_spec(uow: UnitOfWork[Session], auto_repo: SQLAlchemyRepository[UUID, Boat]) -> None:
    boat_a = Boat(name=Name("Alpha"), price=Price(10.0))
    boat_b = Boat(name=Name("Beta"), price=Price(50.0))

    with uow(auto_repo):
        auto_repo.save(boat_a)
        auto_repo.save(boat_b)
        uow.commit()

    with uow(auto_repo):
        results = auto_repo.get_many(Boat.price > 20.0)

    assert len(results) == 1
    assert results[0].name == Name("Beta")


def test_auto_count_with_spec(uow: UnitOfWork[Session], auto_repo: SQLAlchemyRepository[UUID, Boat]) -> None:
    boat_a = Boat(name=Name("Alpha"), price=Price(10.0))
    boat_b = Boat(name=Name("Beta"), price=Price(50.0))
    boat_c = Boat(name=Name("Gamma"), price=Price(80.0))

    with uow(auto_repo):
        auto_repo.save(boat_a)
        auto_repo.save(boat_b)
        auto_repo.save(boat_c)
        uow.commit()

    with uow(auto_repo):
        assert auto_repo.count(Boat.price > 20.0) == 2

    with uow(auto_repo):
        assert auto_repo.count(Boat.price > 100.0) == 0


def test_auto_optimistic_lock_conflict(uow: UnitOfWork[Session], auto_repo: SQLAlchemyRepository[UUID, Boat]) -> None:
    boat = Boat(name=Name("Contested"), price=Price(100.0))

    with uow(auto_repo):
        auto_repo.save(boat)
        uow.commit()

    with uow(auto_repo):
        copy_a = auto_repo.get_one(boat.id)
    with uow(auto_repo):
        copy_b = auto_repo.get_one(boat.id)

    copy_a.price = Price(200.0)
    with uow(auto_repo):
        auto_repo.update(copy_a)
        uow.commit()

    copy_b.price = Price(300.0)
    with pytest.raises(OptimisticLockError):
        with uow(auto_repo):
            auto_repo.update(copy_b)
            uow.commit()


def test_auto_motorboat_save_and_get_one(uow: UnitOfWork[Session], auto_motorboat_repo: SQLAlchemyRepository[UUID, MotorBoat]) -> None:
    boat = make_motorboat("Sea Spirit", boat_price=4_999.99, engine_price=1_200.0)

    with uow(auto_motorboat_repo):
        auto_motorboat_repo.save(boat)
        uow.commit()

    with uow(auto_motorboat_repo):
        fetched = auto_motorboat_repo.get_one(boat.id)

    assert fetched.name == Name("Sea Spirit")
    assert fetched.price == Price(4_999.99)
    assert fetched.engine.price == Price(1_200.0)


def test_auto_motorboat_filter_by_engine_price(uow: UnitOfWork[Session], auto_motorboat_repo: SQLAlchemyRepository[UUID, MotorBoat]) -> None:
    expensive = make_motorboat("Yacht", boat_price=50_000.0, engine_price=5_000.0)
    cheap = make_motorboat("Dinghy", boat_price=500.0, engine_price=200.0)

    with uow(auto_motorboat_repo):
        auto_motorboat_repo.save(expensive)
        auto_motorboat_repo.save(cheap)
        uow.commit()

    with uow(auto_motorboat_repo):
        results = auto_motorboat_repo.get_many(MotorBoat.engine.price > 1_000.0)

    assert len(results) == 1
    assert results[0].name == Name("Yacht")


def test_auto_motorboat_update_engine_price(uow: UnitOfWork[Session], auto_motorboat_repo: SQLAlchemyRepository[UUID, MotorBoat]) -> None:
    boat = make_motorboat("Cruiser", boat_price=10_000.0, engine_price=800.0)

    with uow(auto_motorboat_repo):
        auto_motorboat_repo.save(boat)
        uow.commit()

    boat.update_engine_price(Price(3_000.0))
    with uow(auto_motorboat_repo):
        auto_motorboat_repo.update(boat)
        uow.commit()

    with uow(auto_motorboat_repo):
        fetched = auto_motorboat_repo.get_one(boat.id)

    assert fetched.engine.price == Price(3_000.0)


def test_auto_journey_save_and_get_one_with_checkpoints(
    uow: UnitOfWork[Session],
    auto_journey_repo: SQLAlchemyRepository[UUID, Journey],
) -> None:
    cp1 = Checkpoint(name=Name("Paris"))
    cp2 = Checkpoint(name=Name("Lyon"))
    journey = Journey(name=Name("France Trip"), checkpoints=[cp1, cp2])

    with uow(auto_journey_repo):
        auto_journey_repo.save(journey)
        uow.commit()

    with uow(auto_journey_repo):
        fetched = auto_journey_repo.get_one(journey.id)

    assert fetched.name == Name("France Trip")
    assert len(fetched.checkpoints) == 2
    assert {cp.name for cp in fetched.checkpoints} == {Name("Paris"), Name("Lyon")}


def test_auto_journey_update_checkpoints(uow: UnitOfWork[Session], auto_journey_repo: SQLAlchemyRepository[UUID, Journey]) -> None:
    journey = Journey(name=Name("Tour"), checkpoints=[Checkpoint(name=Name("A"))])

    with uow(auto_journey_repo):
        auto_journey_repo.save(journey)
        uow.commit()

    journey.checkpoints.append(Checkpoint(name=Name("B")))
    with uow(auto_journey_repo):
        auto_journey_repo.update(journey)
        uow.commit()

    with uow(auto_journey_repo):
        fetched = auto_journey_repo.get_one(journey.id)

    assert len(fetched.checkpoints) == 2
