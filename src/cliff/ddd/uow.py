from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

from cliff.ddd.aggregate import Aggregate
from cliff.ddd.repository import IRepository

if TYPE_CHECKING:
    pass


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
        """Begin a transaction."""

    @abstractmethod
    def commit(self) -> None:
        """Commit a transaction."""

    @abstractmethod
    def rollback(self) -> None:
        """Rollback a transaction. abort any uncommited changes"""

    @abstractmethod
    def close(self) -> None:
        """Close a transaction. Use after finishing a transaction successfully"""


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


class UnitOfWork[TSessions, TId]:

    def __init__(
            self,
            context: DBContext[TSessions],
            repo: IRepository[TId, TSessions],
            *,
            auto_commit=False
    ):
        self._context = context
        self.repo = repo
        self._auto_commit = auto_commit

    def __enter__(self) -> Self:
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
