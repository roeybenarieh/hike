"""Oracle Database parity tests via testcontainers.

Oracle uses REGEXP_LIKE with explicit 'i'/'c' match_parameter for case control.
POSIX class tokens ([[:alpha:]] etc.) are Unicode-aware in Oracle, so we rewrite
them to ASCII ranges for parity with re2 and MongoDB PCRE.
"""
from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import Float, ForeignKey, String, Uuid, create_engine, delete as sa_delete
from sqlalchemy.engine import Engine as SAEngine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

try:
    from testcontainers.community.oracle import OracleDbContainer as _OracleDbContainerBase  # pyright: ignore[reportMissingImports]
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy  # pyright: ignore[reportMissingImports]

    class _OracleDbContainer(_OracleDbContainerBase):  # type: ignore[possibly-undefined]
        def _connect(self) -> None:
            LogMessageWaitStrategy("DATABASE IS READY TO USE!").wait_until_ready(self)  # type: ignore[arg-type]

    _oracle_available = True
except ImportError:
    _oracle_available = False

from hike.persistence.providers.sqlalchemy import (
    DictSQLAlchemyMapper,
    SQLAlchemyDBContext,
    SQLAlchemyRepository,
)
from hike.persistence.uow import UnitOfWork

from tests.hike.conftest import Boat, Checkpoint, Journey
from tests.hike.persistence.providers.parity_suite import RepositoryParitySuite

_SKIP_ORACLE = pytest.mark.skipif(
    not _oracle_available,
    reason="Oracle testcontainer not available",
)


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
def oracle_engine() -> Iterator[SAEngine]:  # type: ignore[misc]
    if not _oracle_available:
        pytest.skip("Oracle testcontainer not available")
    with _OracleDbContainer() as oracle:  # type: ignore[possibly-undefined]
        engine = create_engine(oracle.get_connection_url())
        Base.metadata.create_all(engine)
        yield engine  # type: ignore[misc]
        Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def truncate_tables(oracle_engine: SAEngine) -> Iterator[None]:  # type: ignore[misc]
    yield  # type: ignore[misc]
    with oracle_engine.connect() as conn:
        conn.execute(sa_delete(CheckpointModel))
        conn.execute(sa_delete(JourneyModel))
        conn.execute(sa_delete(BoatModel))
        conn.commit()


# ---------------------------------------------------------------------------
# Parity test class
# ---------------------------------------------------------------------------

@_SKIP_ORACLE
class TestOracleRepositoryParity(RepositoryParitySuite):
    @pytest.fixture
    def uow(self, oracle_engine: SAEngine) -> UnitOfWork[Session]:
        factory = sessionmaker(oracle_engine)
        ctx = SQLAlchemyDBContext(factory)
        return UnitOfWork(ctx)

    @pytest.fixture
    def repo(self, oracle_engine: SAEngine) -> SQLAlchemyRepository[UUID, Boat]:  # noqa: ARG002
        return SQLAlchemyRepository(Boat, dict_boat_mapper)

    @pytest.fixture
    def journey_repo(self, oracle_engine: SAEngine) -> SQLAlchemyRepository[UUID, Journey]:  # noqa: ARG002
        return SQLAlchemyRepository(Journey, dict_journey_mapper)

    @pytest.fixture
    def watch_repo(self, oracle_engine: SAEngine) -> Iterator[SQLAlchemyRepository[UUID, Boat]]:
        repo: SQLAlchemyRepository[UUID, Boat] = SQLAlchemyRepository(Boat, dict_boat_mapper)
        sess = sessionmaker(oracle_engine)()
        repo.session = sess
        yield repo
        sess.close()
