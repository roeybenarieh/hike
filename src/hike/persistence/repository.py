from abc import ABC, abstractmethod
from collections.abc import Generator, Iterable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any, Generic, TypeVar, overload, final

from hike import Aggregate
from hike.common import DomainError
from hike.persistence.ordering import OrderBy
from hike.persistence.pagination import Page, Pagination
from hike.persistence.persistable import Persistable
from hike.specifications import ISpecification

TPersistable = TypeVar("TPersistable", bound=Persistable[Any])
TAggregate = TypeVar("TAggregate", bound=Aggregate[Any])
TId = TypeVar("TId")
TSession = TypeVar("TSession")


class RepositoryError(DomainError): ...


class DBConnectionError(RepositoryError): ...


class UnknownError(RepositoryError): ...


class ResourceError(RepositoryError):
    def __init__(self, resource: Any) -> None:
        self.resource = resource


class ResourceDoesNotExistError(ResourceError): ...


class ResourceAlreadyExistError(ResourceError): ...


class OptimisticLockError(ResourceError):
    """Raised when an object has been modified by another writer since it was read.

    Callers should re-fetch the object and retry their operation.
    """


class LockConflictError(ResourceError):
    """Raised when an exclusive lock cannot be acquired within the timeout.

    *resource* is the aggregate id that is already locked.
    *timeout* is the timeout that expired (``None`` means the lock was not
    immediately available on a non-blocking try).
    """

    def __init__(self, resource: Any, timeout: float | None = None) -> None:
        super().__init__(resource)
        self.timeout = timeout


class UnsupportedDialectError(RepositoryError):
    """Raised when a regex specification is used with an unsupported database dialect.

    Supported dialects: postgresql, mysql, mariadb, oracle, sqlite, and mssql (SQL
    Server 2025+, major version 17+).  SQL Server before version 17 does not include
    REGEXP_LIKE and is therefore unsupported.
    """


# HACK: Python has no higher-kinded types, so we cannot statically enforce that
# TId is TPersistable's ID type, nor that TPersistable is parameterized by TId.
# Subclasses must keep them consistent by convention.
class IRepository(Generic[TId, TPersistable, TSession], ABC):

    def _after_mutate(self, obj: TPersistable) -> None:
        """Hook called after every save/update/upsert. Default: no-op.

        ``IAggregateRepository`` overrides this to drain domain events.
        """

    @property
    def session(self) -> TSession:
        if self._session is None:
            raise RuntimeError("Session wasn't provided to the repository")
        return self._session

    @session.setter
    def session(self, value: TSession) -> None:
        self._session = value

    _session: TSession | None = None

    @abstractmethod
    def save(self, obj: TPersistable) -> TId:
        """Save a new object.

        :param obj: The object to save.
        :raise ResourceAlreadyExistError: if an object with the same id already exists.
        """

    def save_many(self, objs: Iterable[TPersistable]) -> list[TId]:
        """Save multiple new objects.

        Delegates to :meth:`save` for each object in order.  Stops at the
        first conflict — objects before it are already persisted.

        :param objs: The objects to save.
        :raise ResourceAlreadyExistError: if any object with the same id already exists.
        """
        return [self.save(obj) for obj in objs]

    @overload
    def delete(self, identifier: TId, /) -> None:
        ...

    @overload
    def delete(self, obj: TPersistable, /) -> None:
        ...

    @final
    def delete(self, id_or_obj: TId | TPersistable, /) -> None:
        """Delete an object.

        :param id_or_obj: The object or its identifier.
        :raise ResourceDoesNotExistError: if the object does not exist.
        :raise OptimisticLockError: if the object was modified since it was read.
        """
        if isinstance(id_or_obj, Persistable):
            self._delete(id_or_obj.get_id())  # type: ignore[arg-type]
        else:
            self._delete(getattr(id_or_obj, "value", id_or_obj))  # type: ignore[arg-type]

    @abstractmethod
    def _delete(self, identifier: TId) -> None:
        """Delete an object by its identifier.

        :param identifier: The identifier of the object to delete.
        :raise ResourceDoesNotExistError: if the object does not exist.
        :raise OptimisticLockError: if the object was modified since it was read.
        """

    @abstractmethod
    def get_one(self, identifier: TId) -> TPersistable:
        """Get one object by id.

        :param identifier: The identifier of the object.
        :raise ResourceDoesNotExistError: if the object does not exist.
        """

    @overload
    def get_many(
            self,
            specification: ISpecification,
    ) -> list[TPersistable]:
        ...

    @overload
    def get_many(
            self,
            specification: ISpecification,
            *,
            ordering: OrderBy | Sequence[OrderBy],
    ) -> list[TPersistable]:
        ...

    @overload
    def get_many(
            self,
            specification: ISpecification,
            *,
            pagination: Pagination,
            ordering: OrderBy | Sequence[OrderBy] | None = ...,
    ) -> Page[TPersistable]:
        ...

    @final
    def get_many(
            self,
            specification: ISpecification,
            *,
            ordering: OrderBy | Sequence[OrderBy] | None = None,
            pagination: Pagination | None = None,
    ) -> list[TPersistable] | Page[TPersistable]:
        """Get multiple objects matching *specification*.

        Without *pagination* returns a ``list[TPersistable]``, optionally sorted
        by *ordering*.  With *pagination* returns a ``Page[TPersistable]``
        containing the items, a total count (``None`` for cursor pagination),
        and next-cursor metadata.

        :param specification: Criteria dictating which objects to return.
        :param ordering: Optional ordering — a single ``OrderBy`` or a sequence
            of them.
        :param pagination: Optional pagination — ``OffsetPagination``,
            ``PagePagination``, or ``CursorPagination``.
        """
        normalized: Sequence[OrderBy] | None
        if isinstance(ordering, OrderBy):
            normalized = [ordering]
        else:
            normalized = ordering
        return self._get_many(specification, ordering=normalized, pagination=pagination)

    @abstractmethod
    def _get_many(
            self,
            specification: ISpecification,
            *,
            ordering: Sequence[OrderBy] | None = None,
            pagination: Pagination | None = None,
    ) -> list[TPersistable] | Page[TPersistable]:
        """Implement ``get_many``.  Override this in concrete repository subclasses."""

    @abstractmethod
    def update(self, obj: TPersistable) -> None:
        """Update a given object.

        Uses optimistic concurrency control: compares the stored version against
        ``get_version(obj)`` and raises if they differ.  On success,
        increments the tracked version via ``set_version``.

        :param obj: The object to update.
        :raise ResourceDoesNotExistError: if the object does not exist.
        :raise OptimisticLockError: if the object was modified since it was read.
        """

    @abstractmethod
    def count(self, specification: ISpecification) -> int:
        """Count objects matching *specification*.

        :param specification: criteria dictating which objects to count.
        """

    @abstractmethod
    def upsert(self, obj: TPersistable) -> None:
        """Update a given object; create it if it does not exist.

        No version check is performed — this is a last-write-wins operation.
        The stored version is incremented unconditionally on update (or set to 0
        on insert); the object's tracked version is intentionally *not* synced back.
        Re-fetch with ``get_one`` before any subsequent version-sensitive writes.

        :param obj: The object to update/create.
        """

    @abstractmethod
    def watch(self, *, include_existing: bool = False) -> Iterator[TPersistable]:
        """Yield persistables as they are inserted, blocking until each one arrives.

        When *include_existing* is True, all records currently in the database
        are yielded first, then streaming continues for new inserts.  Each
        record is yielded exactly once — inserts that arrive concurrently
        during the initial scan are deduplicated by ID.

        Each call returns a fresh, infinite iterator; break out of the loop to
        stop.  Concurrent calls are supported.

        Implementations must not busy-poll: use OS-level blocking (change
        streams, pub/sub) where available, or timed sleep with an interval of
        at least 100 ms as a last resort.

        Example::

            for order in repo.watch():
                process(order)   # called for each new insert; break to stop

        :raise DBConnectionError: if the underlying stream/subscription drops.
        """

    @abstractmethod
    def is_modified(self, obj: TPersistable) -> bool:
        """Return True if the database version of *obj* differs from its local version.

        A True result means another writer has updated the record since *obj*
        was last read.  Callers should re-fetch before any subsequent
        version-sensitive writes.

        :param obj: The object whose version to check.
        :raise ResourceDoesNotExistError: if the object does not exist in the database.
        """

    @abstractmethod
    def acquire_lock(self, id: TId, *, owner: Any = None, timeout: float | None = None) -> None:
        """Acquire an exclusive lock on aggregate *id*.

        If the lock is already held by a different *owner*, block until *timeout*
        seconds elapse (or indefinitely when *timeout* is ``None``), then raise
        :exc:`LockConflictError`.

        Acquiring a lock already held by the same *owner* is a no-op (reentrant).

        The lock TTL (dead-man's switch that auto-expires the lock if the holder
        crashes) is configured on the repository constructor, not here.  Persistent
        providers (Redis, MongoDB, SQLAlchemy) always have a TTL; in-memory does not
        need one because a process crash wipes all state.

        :param id: The identifier of the aggregate to lock.
        :param owner: Opaque token identifying the lock holder (e.g. a saga id).
        :param timeout: Maximum seconds to wait. ``None`` means wait forever.
        :raise LockConflictError: if the lock cannot be acquired within *timeout*.
        """

    @abstractmethod
    def release_lock(self, id: TId, *, owner: Any = None) -> None:
        """Release the lock on aggregate *id*.

        No-ops silently if the lock is not currently held or is held by a
        different *owner*.

        :param id: The identifier of the aggregate to unlock.
        :param owner: Must match the token used when the lock was acquired.
        """

    @contextmanager
    def locked(self, id: TId, *, owner: Any = None, timeout: float | None = None) -> Generator[None, None, None]:
        """Context manager: acquire *id* on enter, release on exit (even on error).

        Usage::

            with repo.locked(order_id, owner=saga_id, timeout=5.0):
                order = repo.get_one(order_id)
                order.start_fulfillment()
                repo.update(order)
        """
        self.acquire_lock(id, owner=owner, timeout=timeout)
        try:
            yield
        finally:
            self.release_lock(id, owner=owner)


class IAggregateRepository(IRepository[TId, TAggregate, TSession], ABC):
    """``IRepository`` extended with domain-event collection for ``Aggregate``-backed repos."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending_events: list[Any] = []

    def get_one(self, identifier: TId) -> TAggregate:
        return super().get_one(getattr(identifier, "value", identifier))  # type: ignore[arg-type]

    def _after_mutate(self, obj: TAggregate) -> None:  # type: ignore[override]
        """Transfer aggregate's raised events into the pending queue and clear the aggregate."""
        self._pending_events.extend(obj.get_events())
        obj.clear_events()

    def drain_events(self) -> list[Any]:
        """Return and clear all pending events. Called by ``UnitOfWork`` on commit."""
        events = list(self._pending_events)
        self._pending_events.clear()
        return events
