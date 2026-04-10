from __future__ import annotations

from typing import Any

from pymongo import MongoClient
from pymongo.client_session import ClientSession
from redis import Redis
from redis.client import Pipeline
from sqlalchemy.orm import Session, sessionmaker

from cliff.ddd.uow import DBContext


class SQLAlchemyDBContext(DBContext):
    """DBContext backed by a SQLAlchemy Session.

    Pass the session to your repositories via ``context.session``.

    Usage::

        factory = sessionmaker(bind=engine)
        ctx = SQLAlchemyDBContext(factory)
        with UnitOfWork(repos, ctx):
            repo.add(aggregate)
            uow.commit()
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    @property
    def session(self) -> Session:
        if self._session is None:
            raise RuntimeError("Transaction not started — call begin() first")
        return self._session

    def begin(self) -> None:
        self._session = self._session_factory()

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def close(self) -> None:
        self.session.close()
        self._session = None


class MongoDBContext(DBContext):
    """DBContext backed by a pymongo ClientSession (ACID transaction).

    Requires a replica set or mongos — standalone MongoDB does not support
    multi-document transactions.

    Pass the session to your repositories via ``context.session``.

    Usage::

        client = MongoClient("mongodb://localhost:27017")
        ctx = MongoDBContext(client)
        with UnitOfWork(repos, ctx):
            repo.add(aggregate)
            uow.commit()
    """

    def __init__(self, client: MongoClient[dict[str, Any]]) -> None:
        self._client = client
        self._session: ClientSession | None = None

    @property
    def session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("Transaction not started — call begin() first")
        return self._session

    def begin(self) -> None:
        self._session = self._client.start_session()
        self._session.start_transaction()

    def commit(self) -> None:
        self.session.commit_transaction()

    def rollback(self) -> None:
        self.session.abort_transaction()

    def close(self) -> None:
        self.session.end_session()
        self._session = None


class RedisDBContext(DBContext):
    """DBContext backed by a redis Pipeline (MULTI/EXEC transaction).

    All commands queued between ``begin()`` and ``commit()`` are sent
    atomically.  ``rollback()`` discards the queued commands (DISCARD).

    Pass the pipeline to your repositories via ``context.pipeline``.

    Usage::

        client = Redis(host="localhost", port=6379)
        ctx = RedisDBContext(client)
        with UnitOfWork(repos, ctx):
            repo.add(aggregate)
            uow.commit()
    """

    def __init__(self, client: Redis) -> None:
        self._client = client
        self._pipeline: Pipeline | None = None

    @property
    def pipeline(self) -> Pipeline:
        if self._pipeline is None:
            raise RuntimeError("Transaction not started — call begin() first")
        return self._pipeline

    def begin(self) -> None:
        self._pipeline = self._client.pipeline(transaction=True)  # pyright: ignore[reportUnknownMemberType]

    def commit(self) -> None:
        self.pipeline.execute()

    def rollback(self) -> None:
        self.pipeline.reset()

    def close(self) -> None:
        self.pipeline.reset()
        self._pipeline = None
