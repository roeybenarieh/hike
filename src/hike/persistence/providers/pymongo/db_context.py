from __future__ import annotations

from typing import Any

from bson.binary import UuidRepresentation
from pymongo import MongoClient
from pymongo.synchronous.client_session import ClientSession

from hike.persistence.uow import DBContext


class PyMongoDBContext(DBContext[ClientSession]):
    """DBContext backed by a pymongo ClientSession (ACID transaction).

    Requires a replica set or mongos — standalone MongoDB does not support
    multi-document transactions.

    The ``client`` must be configured with:

    - ``directConnection=True`` — required for multi-document transaction
      routing to a specific replica-set primary.
    - ``uuidRepresentation="standard"`` — required for correct UUID
      serialisation.

    Usage::

        client = MongoClient(
            "mongodb://localhost:27017",
            directConnection=True,
            uuidRepresentation="standard",
        )
        ctx = PyMongoDBContext(client)
        uow = UnitOfWork(ctx)
        with uow(repo):
            repo.save(aggregate)
            uow.commit()
    """

    def __init__(self, client: MongoClient[dict[str, Any]]) -> None:
        super().__init__()
        if not client.options.direct_connection:
            raise ValueError(
                "PyMongoDBContext requires directConnection=True on the MongoClient. "
                "Pass directConnection=True to MongoClient(...)."
            )
        if client.codec_options.uuid_representation != UuidRepresentation.STANDARD:
            raise ValueError(
                "PyMongoDBContext requires uuidRepresentation='standard' on the MongoClient. "
                "Pass uuidRepresentation='standard' to MongoClient(...)."
            )
        self._client = client

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
