"""MariaDB parity tests via testcontainers.

MariaDB uses the PCRE2-based REGEXP operator (introduced in MariaDB 10.0.5).
Unlike MySQL 8, MariaDB does NOT have a REGEXP_LIKE function with a match_type
argument.  Case sensitivity is controlled via PCRE inline mode modifiers:
  (?i)  — case-insensitive
  (?-i) — case-sensitive (needed because the default depends on column collation)

POSIX character classes ([[:alpha:]], [[:digit:]], etc.) are Unicode-aware in
MariaDB's PCRE when the database uses a UTF-8 character set.  The parity suite's
test_regex_class_alpha_is_unicode_aware therefore applies without override.
"""
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
def mariadb_engine() -> Iterator[SAEngine]:  # type: ignore[misc]
    # MySqlContainer works with MariaDB images because MariaDB accepts the same
    # MYSQL_* environment variables and prints "ready for connections" twice
    # (socket + TCP), satisfying the default MySQL wait strategy.
    # The URL is rewritten from mysql+pymysql:// → mariadb+pymysql:// so that
    # SQLAlchemy selects the MariaDBDialect (dialect.name = "mariadb") instead
    # of the MySQLDialect.  This is required for hike to route regex specs to
    # the REGEXP/PCRE path rather than MySQL's REGEXP_LIKE.
    with MySqlContainer("mariadb:10.11", dialect="pymysql") as mariadb:
        url = mariadb.get_connection_url().replace("mysql+pymysql://", "mariadb+pymysql://")
        engine = create_engine(
            url,
            # READ COMMITTED lets the watch() poll loop see rows committed by
            # other sessions.  MariaDB's default REPEATABLE READ would cause the
            # watcher to hold a snapshot from its first query and never observe
            # rows inserted by the main test thread.
            execution_options={"isolation_level": "READ COMMITTED"},
        )
        Base.metadata.create_all(engine)
        yield engine  # type: ignore[misc]
        Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def truncate_tables(mariadb_engine: SAEngine) -> Iterator[None]:  # type: ignore[misc]
    yield  # type: ignore[misc]
    with mariadb_engine.connect() as conn:
        conn.execute(sa_delete(CheckpointModel))
        conn.execute(sa_delete(JourneyModel))
        conn.execute(sa_delete(BoatModel))
        conn.commit()


# ---------------------------------------------------------------------------
# Parity test class
# ---------------------------------------------------------------------------

class TestMariaDBRepositoryParity(RepositoryParitySuite):
    @pytest.fixture
    def uow(self, mariadb_engine: SAEngine) -> UnitOfWork[Session]:
        factory = sessionmaker(mariadb_engine)
        ctx = SQLAlchemyDBContext(factory)
        return UnitOfWork(ctx)

    @pytest.fixture
    def repo(self, mariadb_engine: SAEngine) -> SQLAlchemyRepository[UUID, Boat]:  # noqa: ARG002
        return SQLAlchemyRepository(Boat, dict_boat_mapper)

    @pytest.fixture
    def journey_repo(self, mariadb_engine: SAEngine) -> SQLAlchemyRepository[UUID, Journey]:  # noqa: ARG002
        return SQLAlchemyRepository(Journey, dict_journey_mapper)

    @pytest.fixture
    def watch_repo(self, mariadb_engine: SAEngine) -> Iterator[SQLAlchemyRepository[UUID, Boat]]:
        repo: SQLAlchemyRepository[UUID, Boat] = SQLAlchemyRepository(Boat, dict_boat_mapper)
        sess = sessionmaker(mariadb_engine)()
        repo.session = sess
        yield repo
        sess.close()
