from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar, overload, final

from hike.ddd.aggregate import Aggregate
from hike.ddd.common import DomainError
from hike.ddd.entity import EntityID
from hike.ddd.specifications import ISpecification

TId = TypeVar("TId")
TSession = TypeVar("TSession")
TAggregate = TypeVar("TAggregate", bound=Aggregate[Any])

_HIKE_VERSION = "__hike_version__"


def get_version(aggregate: Aggregate[Any]) -> int:
    """Return the optimistic-concurrency version tracked by the repository for *aggregate*.

    Returns 0 if the aggregate has never been persisted.
    """
    return aggregate.__dict__.get(_HIKE_VERSION, 0)


def set_version(aggregate: Aggregate[Any], version: int) -> None:
    """Set the repository-managed optimistic-concurrency version on *aggregate*.

    Called exclusively by repository implementations — not intended for domain code.
    """
    aggregate.__dict__[_HIKE_VERSION] = version


class RepositoryError(DomainError): ...


class DBConnectionError(RepositoryError): ...


class UnknownError(RepositoryError): ...


class AggregateError(RepositoryError):
    def __init__(self, aggregate: object) -> None:
        self.aggregate = aggregate


class AggregateDoesNotExistError(AggregateError): ...


class AggregateAlreadyExistError(AggregateError): ...


class OptimisticLockError(AggregateError):
    """Raised when an aggregate has been modified by another writer since it was read.

    Callers should re-fetch the aggregate and retry their operation.
    """


# HACK: Python has no higher-kinded types, so we cannot statically enforce that
# TId is TAggregate's ID type, nor that TAggregate is parameterized by TId.
# Subclasses must keep them consistent by convention.
class IRepository(Generic[TId, TSession, TAggregate], ABC):
    _session: TSession | None = None

    @property
    def session(self) -> TSession:
        if self._session is None:
            raise RuntimeError("Session wasn't provided to the repository")
        return self._session

    @session.setter
    def session(self, value: TSession) -> None:
        self._session = value

    @abstractmethod
    def save(self, aggregate: TAggregate) -> TId:
        """Save a new aggregate.

        :param aggregate: The aggregate to save.
        :raise AggregateAlreadyExistError: if the aggregate already exists.
        """

    @overload
    def delete(self, identifier: EntityID[TId], /) -> None: ...

    @overload
    def delete(self, aggregate: TAggregate, /) -> None: ...

    @final
    def delete(self, id_or_aggregate: EntityID[TId] | TAggregate, /) -> None:
        """Delete an aggregate.

        :param id_or_aggregate: The aggregate or its identifier.
        :raise AggregateDoesNotExistError: if the aggregate does not exist.
        :raise OptimisticLockError: if the aggregate was modified since it was read.
        """
        if isinstance(id_or_aggregate, Aggregate):
            self._delete(id_or_aggregate.id)
        else:
            self._delete(id_or_aggregate)

    @abstractmethod
    def _delete(self, identifier: EntityID[TId]) -> None:
        """Delete an aggregate by its identifier.

        :param identifier: The identifier of the aggregate to delete.
        :raise AggregateDoesNotExistError: if the aggregate does not exist.
        :raise OptimisticLockError: if the aggregate was modified since it was read.
        """

    @abstractmethod
    def get_one(self, identifier: EntityID[TId]) -> TAggregate:
        """Get one aggregate by id.

        :param identifier: The identifier of the aggregate.
        :raise AggregateDoesNotExistError: if the aggregate does not exist.
        """

    @abstractmethod
    def get_many(self, specification: ISpecification) -> list[TAggregate]:
        """Get multiple aggregates matching *specification*.

        Note: query capabilities are limited — extend the repository for complex queries.

        :param specification: criteria dictating which aggregates to return.
        """

    @abstractmethod
    def update(self, aggregate: TAggregate) -> None:
        """Update a given aggregate.

        Uses optimistic concurrency control: compares the stored version against
        ``get_version(aggregate)`` and raises if they differ.  On success,
        increments the tracked version via ``set_version``.

        :param aggregate: The aggregate to update.
        :raise AggregateDoesNotExistError: if the aggregate does not exist.
        :raise OptimisticLockError: if the aggregate was modified since it was read.
        """

    @abstractmethod
    def upsert(self, aggregate: TAggregate) -> None:
        """Update a given aggregate; create it if it does not exist.

        No version check is performed — this is a last-write-wins operation.
        The stored version is incremented unconditionally on update (or set to 0
        on insert); the aggregate's tracked version is intentionally *not* synced back.
        Re-fetch with ``get_one`` before any subsequent version-sensitive writes.

        :param aggregate: The aggregate to update/create.
        """
