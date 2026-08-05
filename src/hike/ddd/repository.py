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


class LockTimeoutError(RepositoryError):
    """Raised when a pessimistic lock cannot be acquired within the allowed time."""


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
        """

    @abstractmethod
    def get_one(self, identifier: EntityID[TId], locked: bool = False) -> TAggregate:
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
    ) -> list[TAggregate]:
        """Get multiple aggregates.

        Note: the query capabilities of this method is limited. extend this repository

        :param specification: specification criteria dictating which aggregate to query.
        :param locked: whether to lock the retrieved aggregates as part of the current transaction.
        """

    @abstractmethod
    def update(self, aggregate: TAggregate) -> None:
        """Update a given aggregate.

        :param aggregate: The aggregate to update.
        :raise AggregateDoesNotExistError: if the aggregate does not exist.
        """

    @abstractmethod
    def upsert(self, aggregate: TAggregate) -> None:
        """Update a given aggregate; create it if it does not exist.

        :param aggregate: The aggregate to update/create.
        """
