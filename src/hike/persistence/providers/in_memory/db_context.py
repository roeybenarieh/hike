from __future__ import annotations

from typing import Any

from hike.persistence.persistable import Persistable
from hike.persistence.uow import DBContext


class InMemoryDBContext(DBContext[dict[Any, Persistable[Any]]]):
    """In-memory DBContext for testing and prototyping.

    Supports rollback by snapshotting committed state on ``begin()``.
    """

    def __init__(self) -> None:
        self._committed: dict[Any, Persistable[Any]] = {}

    def begin(self) -> None:
        self._session = dict(self._committed)

    def commit(self) -> None:
        self._committed = dict(self.session)
        self._session = None

    def rollback(self) -> None:
        self._session = None

    def close(self) -> None:
        self._session = None
