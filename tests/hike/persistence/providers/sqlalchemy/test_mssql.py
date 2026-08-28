"""MSSQL parity tests via testcontainers.

SQL Server 2025 (major version 17) introduces native ``REGEXP_LIKE`` at database
compatibility level 170, backed by the RE2 engine.  The testcontainer uses the
``2025-latest`` image so all parity tests, including regex specs, run natively in SQL.

SQL Server 2022 (version 16) and earlier are not supported — ``UnsupportedDialectError``
is raised when a regex specification is used against an unsupported version.
"""
from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import Float, ForeignKey, String, Uuid, create_engine, delete as sa_delete, text
from sqlalchemy.engine import Engine as SAEngine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from testcontainers.community.mssql import SqlServerContainer  # pyright: ignore[reportMissingImports]

from hike.persistence.providers.sqlalchemy import (
    DictSQLAlchemyMapper,
    SQLAlchemyDBContext,
    SQLAlchemyRepository,
)
from hike.persistence.uow import UnitOfWork

from tests.hike.conftest import Boat, Checkpoint, Journey
from tests.hike.persistence.providers.parity_suite import RepositoryParitySuite


# ---------------------------------------------------------------------------
# ORM models
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
def mssql_engine() -> Iterator[SAEngine]:  # type: ignore[misc]
    with SqlServerContainer("mcr.microsoft.com/mssql/server:2025-latest") as mssql:
        engine = create_engine(
            mssql.get_connection_url(),
            # MSSQL uses SNAPSHOT isolation to avoid read-write deadlocks
            execution_options={"isolation_level": "READ COMMITTED"},
        )
        Base.metadata.create_all(engine)
        yield engine  # type: ignore[misc]
        Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def truncate_tables(mssql_engine: SAEngine) -> Iterator[None]:  # type: ignore[misc]
    yield  # type: ignore[misc]
    with mssql_engine.connect() as conn:
        # MSSQL requires disabling constraints before truncating referenced tables
        conn.execute(text("ALTER TABLE checkpoints NOCHECK CONSTRAINT ALL"))
        conn.execute(sa_delete(CheckpointModel))
        conn.execute(sa_delete(JourneyModel))
        conn.execute(sa_delete(BoatModel))
        conn.execute(text("ALTER TABLE checkpoints CHECK CONSTRAINT ALL"))
        conn.commit()


# ---------------------------------------------------------------------------
# Parity test class
# ---------------------------------------------------------------------------

class TestMSSQLRepositoryParity(RepositoryParitySuite):
    @pytest.fixture
    def uow(self, mssql_engine: SAEngine) -> UnitOfWork[Session]:
        factory = sessionmaker(mssql_engine)
        ctx = SQLAlchemyDBContext(factory)
        return UnitOfWork(ctx)

    @pytest.fixture
    def repo(self, mssql_engine: SAEngine) -> SQLAlchemyRepository[UUID, Boat]:  # noqa: ARG002
        return SQLAlchemyRepository(Boat, dict_boat_mapper)

    @pytest.fixture
    def journey_repo(self, mssql_engine: SAEngine) -> SQLAlchemyRepository[UUID, Journey]:  # noqa: ARG002
        return SQLAlchemyRepository(Journey, dict_journey_mapper)

    @pytest.fixture
    def watch_repo(self, mssql_engine: SAEngine) -> Iterator[SQLAlchemyRepository[UUID, Boat]]:
        repo: SQLAlchemyRepository[UUID, Boat] = SQLAlchemyRepository(Boat, dict_boat_mapper)
        sess = sessionmaker(mssql_engine)()
        repo.session = sess
        yield repo
        sess.close()


