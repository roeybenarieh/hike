from __future__ import annotations

from typing import Any

from pymongo import MongoClient
from pymongo.synchronous.client_session import ClientSession

from hike.ddd.uow import DBContext


class PyMongoDBContext(DBContext[ClientSession]):
    """DBContext backed by a pymongo ClientSession (ACID transaction).

    Requires a replica set or mongos — standalone MongoDB does not support
    multi-document transactions.

    Usage::

        client = MongoClient("mongodb://localhost:27017")
        ctx = PyMongoDBContext(client)
        with UnitOfWork(repos, ctx):
            repo.add(aggregate)
            uow.commit()
    """

    def __init__(self, client: MongoClient[dict[str, Any]]) -> None:
        super().__init__()
        host, port = next(iter(client.topology_description.server_descriptions()))
        self._client: MongoClient[dict[str, Any]] = MongoClient(
            host=host,
            port=port,
            directConnection=True,
            uuidRepresentation="standard",
        )

    @property
    def client(self) -> MongoClient[dict[str, Any]]:
        return self._client

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
