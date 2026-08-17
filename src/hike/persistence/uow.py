from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Any, Self

from hike.domain_event import DomainEvent, EventBus
from hike.persistence.outbox import IOutboxRepository
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


class UnitOfWork[TSessions]:

    def __init__(self, context: DBContext[TSessions]) -> None:
        self._context = context
        self._repos: tuple[IRepository[Any, TSessions, Any], ...] = ()
        self._auto_commit: bool = False
        self._bus: EventBus | None = None
        self._outbox: IOutboxRepository | None = None

    def __call__(
            self,
            *repos: IRepository[Any, TSessions, Any],
            auto_commit: bool = False,
            bus: EventBus | None = None,
            outbox: IOutboxRepository | None = None,
    ) -> Self:
        self._repos = repos
        self._auto_commit = auto_commit
        self._bus = bus
        self._outbox = outbox
        return self

    def __enter__(self) -> Self:
        if not self._repos:
            raise ValueError("UnitOfWork requires at least one repository")
        self._context.begin()
        for repo in self._repos:
            repo.session = self._context.session
        if self._outbox is not None:
            self._outbox.session = self._context.session
        return self

    def __exit__(
            self,
            exc_type: type[BaseException] | None,
            _exc_val: BaseException | None,
            _exc_tb: TracebackType | None,
    ) -> None:
        auto_commit = self._auto_commit
        if exc_type:
            self._repos = ()
            self._auto_commit = False
            self._bus = None
            self._outbox = None
            self._context.rollback()
            return
        if auto_commit:
            self._auto_commit = False
            try:
                self.commit()
            finally:
                self._repos = ()
                self._bus = None
                self._outbox = None
            return
        self._repos = ()
        self._auto_commit = False
        self._bus = None
        self._outbox = None
        self._context.close()

    def _collect_all_events(self) -> list[DomainEvent]:
        events: list[DomainEvent] = []
        for repo in self._repos:
            events.extend(repo.drain_events())
        return events

    def commit(self) -> None:
        events = self._collect_all_events()
        if self._bus is not None:
            self._bus.publish_all(events)   # handlers run first; error here aborts the commit
        if self._outbox is not None:
            self._outbox.save_all(events)
        self._context.commit()
