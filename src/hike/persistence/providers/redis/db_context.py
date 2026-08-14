from __future__ import annotations

from redis import Redis
from redis.client import Pipeline

from hike.persistence.uow import DBContext


class RedisDBContext(DBContext[Pipeline]):
    """DBContext backed by a redis Pipeline (MULTI/EXEC transaction).

    All commands queued between ``begin()`` and ``commit()`` are sent
    atomically.  ``rollback()`` discards the queued commands (DISCARD).

    Usage::

        client = Redis(host="localhost", port=6379)
        ctx = RedisDBContext(client)
        with UnitOfWork(repos, ctx):
            repo.add(aggregate)
            uow.commit()
    """

    def __init__(self, client: Redis) -> None:
        super().__init__()
        self._client = client

    def begin(self) -> None:
        self._session = self._client.pipeline(transaction=True)  # pyright: ignore[reportUnknownMemberType]

    def commit(self) -> None:
        self.session.execute()

    def rollback(self) -> None:
        self.session.reset()

    def close(self) -> None:
        self.session.reset()
        self._session = None
