from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from cliff.ddd.common import DomainError
from cliff.ddd.aggregate import Aggregate
from cliff.ddd.specifications import ISpecification

TId = TypeVar("TId")
TAggregate = TypeVar("TAggregate", bound=Aggregate)


class RepositoryError(DomainError): ...


class DBConnectionError(RepositoryError): ...


class UnknownError(RepositoryError): ...


class AggregateError(RepositoryError):
    def __init__(self, aggregate: Aggregate):
        self.aggregate = aggregate


class AggregateDoesNotExistError(AggregateError): ...


class AggregateAlreadyExistError(AggregateError): ...


# HACK: as of time of writing this class, there is no way to statically enforce
# that TId is really the id type of TAggregate (no higher-kinded types in Python).
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
        """Get one aggregate by id.

        :param identifier: The identifier of the aggregate.
        :param locked: whether to lock the retrieved aggregate as part of the current transaction.
        :raise AggregateDoesNotExistError: if the aggregate does not exist.
        """

    @abstractmethod
    def get_many(
        self,
        specifications: ISpecification,
        only: Any = None,
        order: Any = None,
        locked: bool = False,
        pagination: Any = None,
    ) -> list[TAggregate]:
        """Get multiple aggregates.

        :param locked: whether to lock the retrieved aggregates as part of the current transaction.
        :param pagination: pagination parameters.
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

    # TODO: think about how exactly to implement this
    @abstractmethod
    def convertion_table(self) -> dict[str, str]:
        """Convert from domain model to table model."""
