from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from hike.ddd.aggregate import Aggregate
from hike.ddd.common import DomainError
from hike.ddd.entity import EntityID
from hike.ddd.specifications import ISpecification

TId = TypeVar("TId")
TSession = TypeVar("TSession")
TAggregate = TypeVar("TAggregate", bound=Aggregate[Any])


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

    @abstractmethod
    def delete(self, aggregate: TAggregate) -> None:
        """Delete an aggregate.

        :param aggregate: The aggregate to delete.
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

        Uses optimistic concurrency control: compares ``aggregate.version`` against
        the stored version and raises if they differ.  On success, increments
        ``aggregate.version`` to match the newly stored value.

        :param aggregate: The aggregate to update.
        :raise AggregateDoesNotExistError: if the aggregate does not exist.
        :raise OptimisticLockError: if the aggregate was modified since it was read.
        """

    @abstractmethod
    def upsert(self, aggregate: TAggregate) -> None:
        """Update a given aggregate; create it if it does not exist.

        No version check is performed — this is a last-write-wins operation.
        The stored version is incremented unconditionally on update (or set to 0
        on insert), but ``aggregate.version`` is intentionally *not* synced back.
        Re-fetch with ``get_one`` before any subsequent version-sensitive writes.

        :param aggregate: The aggregate to update/create.
        """
