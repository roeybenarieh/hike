"""MySQL 8.0 parity tests via testcontainers."""
from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import Float, ForeignKey, String, Uuid, create_engine, delete as sa_delete
from sqlalchemy.engine import Engine as SAEngine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from testcontainers.community.mysql import MySqlContainer  # pyright: ignore[reportMissingImports]

from hike.persistence.providers.sqlalchemy import (
    DictSQLAlchemyMapper,
    SQLAlchemyDBContext,
    SQLAlchemyRepository,
)
from hike.persistence.uow import UnitOfWork

from tests.hike.conftest import Boat, Checkpoint, Journey
from tests.hike.persistence.providers.parity_suite import RepositoryParitySuite


# ---------------------------------------------------------------------------
# ORM models — MySQL uses CHAR(36) for UUIDs (no native UUID type)
# ---------------------------------------------------------------------------

class Base(DeclarativeBase): ...


class BoatModel(Base):
    __tablename__ = "boats"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)


class CheckpointModel(Base):
    __tablename__ = "checkpoints"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    journey_id: Mapped[UUID] = mapped_column(ForeignKey("journeys.id"), nullable=False)


class JourneyModel(Base):
    __tablename__ = "journeys"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    checkpoints: Mapped[list[CheckpointModel]] = relationship(
        "CheckpointModel", cascade="all, delete-orphan", lazy="joined"
    )


# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------

dict_boat_mapper = DictSQLAlchemyMapper({Boat: BoatModel})
dict_journey_mapper = DictSQLAlchemyMapper({Journey: JourneyModel, Checkpoint: CheckpointModel})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def mysql_engine() -> Iterator[SAEngine]:  # type: ignore[misc]
    with MySqlContainer("mysql:8.0") as mysql:
        url = mysql.get_connection_url().replace("mysql://", "mysql+pymysql://")
        engine = create_engine(
            url,
            # READ COMMITTED lets the watch() poll loop see rows committed by
            # other sessions.  MySQL's default REPEATABLE READ would cause the
            # watcher to hold a snapshot from its first query and never observe
            # rows inserted by the main test thread.
            execution_options={"isolation_level": "READ COMMITTED"},
        )
        Base.metadata.create_all(engine)
        yield engine  # type: ignore[misc]
        Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def truncate_tables(mysql_engine: SAEngine) -> Iterator[None]:  # type: ignore[misc]
    yield  # type: ignore[misc]
    with mysql_engine.connect() as conn:
        conn.execute(sa_delete(CheckpointModel))
        conn.execute(sa_delete(JourneyModel))
        conn.execute(sa_delete(BoatModel))
        conn.commit()


# ---------------------------------------------------------------------------
# Parity test class
# ---------------------------------------------------------------------------

class TestMySQLRepositoryParity(RepositoryParitySuite):
    @pytest.fixture
    def uow(self, mysql_engine: SAEngine) -> UnitOfWork[Session]:
        factory = sessionmaker(mysql_engine)
        ctx = SQLAlchemyDBContext(factory)
        return UnitOfWork(ctx)

    @pytest.fixture
    def repo(self, mysql_engine: SAEngine) -> SQLAlchemyRepository[UUID, Boat]:  # noqa: ARG002
        return SQLAlchemyRepository(Boat, dict_boat_mapper)

    @pytest.fixture
    def journey_repo(self, mysql_engine: SAEngine) -> SQLAlchemyRepository[UUID, Journey]:  # noqa: ARG002
        return SQLAlchemyRepository(Journey, dict_journey_mapper)

    @pytest.fixture
    def watch_repo(self, mysql_engine: SAEngine) -> Iterator[SQLAlchemyRepository[UUID, Boat]]:
        repo: SQLAlchemyRepository[UUID, Boat] = SQLAlchemyRepository(Boat, dict_boat_mapper)
        sess = sessionmaker(mysql_engine)()
        repo.session = sess
        yield repo
        sess.close()
