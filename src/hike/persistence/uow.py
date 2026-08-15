from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Any, Self

from hike.aggregate import Aggregate
from hike.persistence.repository import IRepository


class DBContext[TSession](ABC):
    """Database session management object."""

    _session: TSession | None = None

    @property
    def session(self) -> TSession:
        if self._session is None:
            raise RuntimeError("Transaction not started — call begin() first")
        return self._session

    @abstractmethod
    def begin(self) -> None:
        """Begin a transaction. Creates and makes ``session`` available."""

    @abstractmethod
    def commit(self) -> None:
        """Commit a transaction."""

    @abstractmethod
    def rollback(self) -> None:
        """Rollback a transaction. abort any uncommited changes"""

    @abstractmethod
    def close(self) -> None:
        """Close a transaction after finishing successfully. Destroys ``session`` and releases any held resources."""


class UnitOfWork[TSessions, TId, TAggregate: Aggregate[Any]]:

    def __init__(
            self,
            context: DBContext[TSessions],
            repo: IRepository[TId, TSessions, TAggregate]
    ):
        self._context = context
        self.repo = repo

    def __enter__(self, auto_commit = False) -> Self:
        self._auto_commit = auto_commit
        self._context.begin()
        self.repo.session = self._context.session
        return self

    def __exit__(
            self,
            exc_type: type[BaseException] | None,
            _exc_val: BaseException | None,
            _exc_tb: TracebackType | None,
    ) -> None:
        if exc_type:
            self._context.rollback()
            return
        if self._auto_commit:
            self.commit()
            return
        self._context.close()

    def commit(self) -> None:
        self._context.commit()
