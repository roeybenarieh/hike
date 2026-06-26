from __future__ import annotations

from typing import Any

from hike.ddd.aggregate import Aggregate
from hike.ddd.uow import DBContext


class InMemoryDBContext(DBContext[dict[Any, Aggregate[Any]]]):
    """In-memory DBContext for testing and prototyping.

    Supports rollback by snapshotting committed state on ``begin()``.
    """

    def __init__(self) -> None:
        self._committed: dict[Any, Aggregate[Any]] = {}

    def begin(self) -> None:
        self._session = dict(self._committed)

    def commit(self) -> None:
        self._committed = dict(self.session)
        self._session = None

    def rollback(self) -> None:
        self._session = None

    def close(self) -> None:
        self._session = None
