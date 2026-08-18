from __future__ import annotations

from typing import Any, cast

from pymongo import MongoClient
from pymongo.synchronous.client_session import ClientSession

from hike.persistence.uow import DBContext


class PyMongoDBContext(DBContext[ClientSession]):
    """DBContext backed by a pymongo ClientSession (ACID transaction).

    Requires a replica set or mongos — standalone MongoDB does not support
    multi-document transactions.

    Usage::

        client = MongoClient("mongodb://localhost:27017")
        ctx = PyMongoDBContext(client)
        uow = UnitOfWork(ctx)
        with uow(repo):
            repo.save(aggregate)
            uow.commit()
    """

    def __init__(self, client: MongoClient[dict[str, Any]]) -> None:
        super().__init__()
        host, port = next(iter(client.topology_description.server_descriptions()))
        # pool_options._credentials is a pymongo private attribute; cast to Any
        # so we can forward username/password when recreating the client with
        # directConnection=True and uuidRepresentation="standard".
        credentials: Any = cast(Any, client.options.pool_options)._credentials
        auth_kwargs: dict[str, Any] = (
            {
                "username": credentials.username,
                "password": credentials.password,
                "authSource": credentials.source,
            }
            if credentials is not None
            else {}
        )
        self._client: MongoClient[dict[str, Any]] = MongoClient(
            host=host,
            port=port,
            directConnection=True,
            uuidRepresentation="standard",
            **auth_kwargs,
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
