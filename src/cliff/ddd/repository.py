from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from cliff.ddd.common import DomainError
from cliff.ddd.aggregate import Aggregate, AggregateID

TId = TypeVar("TId", bound=AggregateID)
TAggregate = TypeVar("TAggregate", bound=Aggregate)


class RepositoryError(DomainError): ...


class DBConnectionError(RepositoryError): ...


class UnknownError(RepositoryError): ...


class AggregateError(RepositoryError):
    def __init__(self, aggregate: Aggregate):
        self.aggregate = aggregate


class AggregateDoesNotExistError(AggregateError): ...


class AggregateAlreadyExistError(AggregateError): ...


# HACK: as of time of writing this class, there is no way to staticlly enforce that the TId is realy the id of the TAggregate
class IRepository(Generic[TId, TAggregate], ABC):
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
    def get_one(self, identifier: TId, locked: bool = False) -> TAggregate:
        """Get on aggregate by id.

        :param identifier: The identifier of the aggregate
        :param locked: whether to lock the retrived aggregate as part of the current transaction.
        :raise AggregateDoesNotExistError: if the aggregate does not exists.
        """

    @abstractmethod
    def get_many(
        self, specifications, locked: bool = False, pagination=None
    ) -> list[TAggregate]:
        """Get on multiple aggregates.

        :param locked: whether to lock the retrived aggregates as part of the current transaction.
        :param pagination: ...
        """

    @abstractmethod
    def update(self, aggregate: TAggregate) -> None:
        """Update a given aggregate.

        :param aggregate: The aggregate to update
        :raise AggregateDoesNotExistError: if the aggregate does not exists.
        """

    @abstractmethod
    def upsert(self, aggregate: TAggregate) -> None:
        """Update a given aggregate, if it doesn't exists than the entity is created.

        :param aggregate: The aggregate to update/create
        """
