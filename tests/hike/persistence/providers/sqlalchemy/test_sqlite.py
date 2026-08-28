"""SQLite parity tests using a file-based database with Python-registered REGEXP/IREGEXP UDFs."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
import re2
from sqlalchemy import Float, String, Uuid, create_engine, delete as sa_delete, event, text
from sqlalchemy.engine import Engine as SAEngine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

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
    __tablename__ = "boats_sqlite"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)


class CheckpointModel(Base):
    __tablename__ = "checkpoints_sqlite"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    journey_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)


class JourneyModel(Base):
    __tablename__ = "journeys_sqlite"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    checkpoints: Mapped[list[CheckpointModel]] = relationship(
        "CheckpointModel",
        cascade="all, delete-orphan",
        lazy="joined",
        primaryjoin="JourneyModel.id == CheckpointModel.journey_id",
        foreign_keys="[CheckpointModel.journey_id]",
    )


# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------

dict_boat_mapper = DictSQLAlchemyMapper({Boat: BoatModel})
dict_journey_mapper = DictSQLAlchemyMapper({Journey: JourneyModel, Checkpoint: CheckpointModel})


# ---------------------------------------------------------------------------
# SQLite REGEXP / IREGEXP UDF registration
# ---------------------------------------------------------------------------
# SQLite has no built-in regex engine.  We register two Python UDFs using
# RE2 so that POSIX ERE semantics (ASCII-only character classes) match every
# other provider.
#
# The function signatures must match the call sites in the SQLAlchemy visitor:
#   REGEXP(pattern, value)  — infix operator form: value REGEXP pattern
#   IREGEXP(pattern, value) — function call form: IREGEXP(pattern, col)

def _register_regexp_udfs(dbapi_connection: Any, _connection_record: Any) -> None:
    """Register REGEXP and IREGEXP as Python UDFs backed by RE2.

    Both functions use the same compile flags as ``RegexSpecification``:
    no MULTILINE (^ / $ are string anchors) and ASCII-only POSIX classes,
    so results are identical to in-memory evaluation and every other backend.
    """

    def regexp(pattern: str, value: str) -> bool:
        try:
            return re2.search(pattern, value) is not None
        except Exception:
            return False

    _icase = re2.Options()
    _icase.case_sensitive = False

    def iregexp(pattern: str, value: str) -> bool:
        try:
            return re2.search(pattern, value, _icase) is not None
        except Exception:
            return False

    dbapi_connection.create_function("REGEXP", 2, regexp)
    dbapi_connection.create_function("IREGEXP", 2, iregexp)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def sqlite_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    # Use as_posix() so the SQLite URL contains forward slashes on Windows too.
    return (tmp_path_factory.mktemp("sqlitedb") / "test.db").as_posix()


@pytest.fixture(scope="session")
def sqlite_engine(sqlite_path: str) -> Iterator[SAEngine]:
    engine = create_engine(
        f"sqlite:///{sqlite_path}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _register_regexp_udfs)
    # WAL mode lets a writer and a reader run concurrently, which is required
    # for watch() tests that poll from a separate thread.
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.commit()
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def truncate_tables(sqlite_engine: SAEngine) -> Iterator[None]:
    yield
    with sqlite_engine.connect() as conn:
        conn.execute(sa_delete(CheckpointModel))
        conn.execute(sa_delete(JourneyModel))
        conn.execute(sa_delete(BoatModel))
        conn.commit()


# ---------------------------------------------------------------------------
# Parity test class
# ---------------------------------------------------------------------------

class TestSQLiteRepositoryParity(RepositoryParitySuite):
    @pytest.fixture
    def uow(self, sqlite_engine: SAEngine) -> UnitOfWork[Session]:
        factory = sessionmaker(sqlite_engine)
        ctx = SQLAlchemyDBContext(factory)
        return UnitOfWork(ctx)

    @pytest.fixture
    def repo(self, sqlite_engine: SAEngine) -> SQLAlchemyRepository[UUID, Boat]:  # noqa: ARG002
        return SQLAlchemyRepository(Boat, dict_boat_mapper)

    @pytest.fixture
    def journey_repo(self, sqlite_engine: SAEngine) -> SQLAlchemyRepository[UUID, Journey]:  # noqa: ARG002
        return SQLAlchemyRepository(Journey, dict_journey_mapper)

    @pytest.fixture
    def watch_repo(self, sqlite_path: str) -> Iterator[SQLAlchemyRepository[UUID, Boat]]:
        # A separate engine with AUTOCOMMIT so the polling loop sees rows
        # committed by the main test session.  SQLite has no READ COMMITTED
        # isolation; AUTOCOMMIT gives each SELECT a fresh read of the database.
        watch_engine = create_engine(
            f"sqlite:///{sqlite_path}",
            connect_args={"check_same_thread": False},
            execution_options={"isolation_level": "AUTOCOMMIT"},
        )
        event.listen(watch_engine, "connect", _register_regexp_udfs)
        repo: SQLAlchemyRepository[UUID, Boat] = SQLAlchemyRepository(Boat, dict_boat_mapper)
        sess = sessionmaker(watch_engine)()
        repo.session = sess
        yield repo
        sess.close()
        watch_engine.dispose()
