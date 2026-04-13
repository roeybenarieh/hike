from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from hike.ddd.aggregate import Aggregate
from hike.ddd.common import DomainError
from hike.ddd.specifications import ISpecification

TId = TypeVar("TId")
TSession = TypeVar("TSession")


class RepositoryError(DomainError): ...


class DBConnectionError(RepositoryError): ...


class UnknownError(RepositoryError): ...


class AggregateError(RepositoryError):
    def __init__(self, aggregate: object) -> None:
        self.aggregate = aggregate


class AggregateDoesNotExistError(AggregateError): ...


class AggregateAlreadyExistError(AggregateError): ...


class LockTimeoutError(RepositoryError):
    """Raised when a pessimistic lock cannot be acquired within the allowed time."""


# HACK: as of time of writing this class, there is no way to statically enforce
# that TId is really the id type of TAggregate (no higher-kinded types in Python).
class IRepository(Generic[TId, TSession], ABC):
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
    def save(self, aggregate: Aggregate[TId]) -> TId:
        """Save a new aggregate.

        :param aggregate: The aggregate to save.
        :raise AggregateAlreadyExistError: if the aggregate already exists.
        """

    @abstractmethod
    def delete(self, aggregate: Aggregate[TId]) -> None:
        """Delete an aggregate.

        :param aggregate: The aggregate to delete.
        :raise AggregateDoesNotExistError: if the aggregate does not exist.
        """

    @abstractmethod
    def get_one(self, identifier: TId, locked: bool = False) -> Aggregate[TId]:
        """Get one aggregate by id.

        :param identifier: The identifier of the aggregate.
        :param locked: whether to lock the retrieved aggregate as part of the current transaction.
        :raise AggregateDoesNotExistError: if the aggregate does not exist.
        """

    @abstractmethod
    def get_many(
            self,
            specification: ISpecification,
            locked: bool = False,
    ) -> list[Aggregate[TId]]:
        """Get multiple aggregates.

        Note: the query capabilities of this method is limited. extend this repository

        :param specification: specification criteria dictating which aggregate to query.
        :param locked: whether to lock the retrieved aggregates as part of the current transaction.
        """

    @abstractmethod
    def update(self, aggregate: Aggregate[TId]) -> None:
        """Update a given aggregate.

        :param aggregate: The aggregate to update.
        :raise AggregateDoesNotExistError: if the aggregate does not exist.
        """

    @abstractmethod
    def upsert(self, aggregate: Aggregate[TId]) -> None:
        """Update a given aggregate; create it if it does not exist.

        :param aggregate: The aggregate to update/create.
        """


class InMemoryRepository(IRepository[TId, dict[Any, "Aggregate[Any]"]]):
    """In-memory repository for testing and prototyping.

    Stores aggregates in the session dict (keyed by raw ``aggregate.id.value``),
    which is managed by ``InMemoryDBContext`` to support rollback.
    """

    def save(self, aggregate: Aggregate[TId]) -> TId:
        key = aggregate.id.value
        if key in self.session:
            raise AggregateAlreadyExistError(aggregate)
        self.session[key] = aggregate
        return aggregate.id  # pyright: ignore[reportReturnType]

    def delete(self, aggregate: Aggregate[TId]) -> None:
        key = aggregate.id.value
        if key not in self.session:
            raise AggregateDoesNotExistError(aggregate)
        del self.session[key]

    def get_one(self, identifier: TId, locked: bool = False) -> Aggregate[TId]:
        aggregate = self.session.get(identifier)
        if aggregate is None:
            raise AggregateDoesNotExistError(identifier)
        return aggregate

    def get_many(
            self,
            specification: ISpecification,
            locked: bool = False,
    ) -> list[Aggregate[TId]]:
        return [agg for agg in self.session.values() if specification.is_satisfied(agg)]

    def update(self, aggregate: Aggregate[TId]) -> None:
        key = aggregate.id.value
        if key not in self.session:
            raise AggregateDoesNotExistError(aggregate)
        self.session[key] = aggregate

    def upsert(self, aggregate: Aggregate[TId]) -> None:
        self.session[aggregate.id.value] = aggregate
