"""Subprocess helper functions for cross-process integration tests.

These functions run in a freshly spawned process (multiprocessing spawn context).
Imports are deferred to function bodies so this module is safe to import with no
side effects, and each helper is self-contained.
"""
def sa_insert_boat(sa_url: str, name: str, price: float) -> None:
    """Insert a Boat via SQLAlchemyRepository into the PostgreSQL boats table."""
    from uuid import UUID

    from sqlalchemy import Float, String, Uuid, create_engine
    from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

    from hike.persistence.providers.sqlalchemy import (
        DictSQLAlchemyMapper,
        SQLAlchemyDBContext,
        SQLAlchemyRepository,
    )
    from hike.persistence.uow import UnitOfWork
    from tests.hike.conftest import Boat, Name, Price

    class _Base(DeclarativeBase): ...

    class _BoatModel(_Base):
        __tablename__ = "boats"
        id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
        name: Mapped[str] = mapped_column(String, nullable=False)
        price: Mapped[float] = mapped_column(Float, nullable=False)

    mapper = DictSQLAlchemyMapper({Boat: _BoatModel})
    engine = create_engine(sa_url)
    repo: SQLAlchemyRepository[UUID, Boat] = SQLAlchemyRepository(Boat, mapper)
    uow: UnitOfWork[Session] = UnitOfWork(SQLAlchemyDBContext(sessionmaker(engine)))

    with uow(repo):
        repo.save(Boat(name=Name(name), price=Price(price)))
        uow.commit()


def pymongo_insert_boat(
    host: str,
    port: int,
    db_name: str,
    col_name: str,
    name: str,
    price: float,
) -> None:
    """Insert a Boat via PyMongoRepository into the MongoDB collection."""
    from uuid import UUID

    from pymongo import MongoClient
    from pymongo.synchronous.client_session import ClientSession

    from hike.persistence.providers.pymongo import PyMongoDBContext, PyMongoRepository
    from hike.persistence.uow import UnitOfWork
    from tests.hike.conftest import Boat, Name, Price

    client: MongoClient[dict[str, object]] = MongoClient(
        host=host, port=port, directConnection=True, uuidRepresentation="standard"
    )
    try:
        col = client[db_name][col_name]
        repo: PyMongoRepository[UUID, Boat] = PyMongoRepository(col, Boat)
        uow: UnitOfWork[ClientSession] = UnitOfWork(PyMongoDBContext(client))
        with uow(repo):
            repo.save(Boat(name=Name(name), price=Price(price)))
            uow.commit()
    finally:
        client.close()


def redis_insert_boat(
    host: str,
    port: int,
    key_prefix: str,
    name: str,
    price: float,
) -> None:
    """Insert a Boat via RedisRepository and publish on the watch channel."""
    from uuid import UUID

    from redis import Redis
    from redis.client import Pipeline

    from hike.persistence.providers.redis import RedisDBContext, RedisRepository
    from hike.persistence.uow import UnitOfWork
    from tests.hike.conftest import Boat, Name, Price

    client: Redis = Redis(host=host, port=port)  # type: ignore[type-arg]
    try:
        repo: RedisRepository[UUID, Boat] = RedisRepository(client, Boat, key_prefix)
        uow: UnitOfWork[Pipeline] = UnitOfWork(RedisDBContext(client))
        with uow(repo):
            repo.save(Boat(name=Name(name), price=Price(price)))
            uow.commit()
    finally:
        client.close()
