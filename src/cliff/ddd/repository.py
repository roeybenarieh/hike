from abc import ABC, abstractmethod
from dataclasses import asdict, fields as dc_fields
from typing import Any, Generic, TypeVar

from pymongo.collection import Collection
from pymongo.synchronous.client_session import ClientSession

from cliff.ddd.aggregate import Aggregate
from cliff.ddd.common import DomainError
from cliff.ddd.specifications import ISpecification
from cliff.ddd.specifications.evaluation_visitors import MongoDBEvaluationVisitor

TId = TypeVar("TId")


class RepositoryError(DomainError): ...


class DBConnectionError(RepositoryError): ...


class UnknownError(RepositoryError): ...


class AggregateError(RepositoryError):
    def __init__(self, aggregate: Aggregate[Any]):
        self.aggregate = aggregate


class AggregateDoesNotExistError(AggregateError): ...


class AggregateAlreadyExistError(AggregateError): ...


# HACK: as of time of writing this class, there is no way to statically enforce
# that TId is really the id type of TAggregate (no higher-kinded types in Python).
class IRepository(Generic[TId], ABC):
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
            specifications: ISpecification,
            locked: bool = False,
    ) -> list[Aggregate[TId]]:
        """Get multiple aggregates.

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


class MongoDBRepository(IRepository[TId]):
    """Generic MongoDB repository backed by ``dataclasses.asdict`` serialization.

    Serialization uses ``asdict(aggregate)`` (deep dict conversion).
    Deserialization filters the MongoDB document to the aggregate's
    ``__init__``-eligible fields and unpacks them with ``aggregate_class(**doc)``.

    ``locked`` is accepted for interface compatibility but ignored — MongoDB
    does not support row-level pessimistic locking.  Use optimistic concurrency
    (version fields) if you need conflict detection.

    ``only`` accepts a list of field names or a pymongo projection dict.
    ``order`` is passed directly to ``cursor.sort()`` (pymongo sort spec).
    ``pagination`` accepts any object or dict with ``skip``/``offset`` and
    ``limit`` attributes or keys.
    """

    def __init__(
            self,
            session: ClientSession,
            collection: Collection[dict[str, Any]],
            aggregate_class: type[Aggregate[TId]],
    ) -> None:
        self._session = session
        self._collection = collection
        self._aggregate_class = aggregate_class

    def _from_doc(self, document: dict[str, Any]) -> Aggregate[TId]:
        """Reconstruct an aggregate from a MongoDB document.

        Filters the document to only the fields accepted by ``__init__``
        (excludes ``_id`` and any ``init=False`` dataclass fields such as
        ``_events``), then unpacks into ``aggregate_class(**…)``.
        """
        init_fields = {f.name for f in dc_fields(self._aggregate_class) if f.init}  # pyright: ignore[reportArgumentType]
        doc = {k: v for k, v in document.items() if k in init_fields}
        return self._aggregate_class(**doc)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # IRepository implementation
    # ------------------------------------------------------------------

    def save(self, aggregate: Aggregate[TId]) -> TId:
        doc = asdict(aggregate)
        try:
            result = self._collection.insert_one(doc, session=self._session)
        except Exception as exc:
            raise AggregateAlreadyExistError(aggregate) from exc
        if not result.acknowledged:
            raise UnknownError("insert_one not acknowledged")
        return aggregate.id  # pyright: ignore[reportReturnType]

    def delete(self, aggregate: Aggregate[TId]) -> None:
        result = self._collection.delete_one(
            {"id": aggregate.id},
            session=self._session,
        )
        if result.deleted_count == 0:
            raise AggregateDoesNotExistError(aggregate)

    def get_one(self, identifier: TId, locked: bool = False) -> Aggregate[TId]:
        document = self._collection.find_one({"id": {"value": identifier}}, session=self._session)
        if document is None:
            raise AggregateDoesNotExistError(identifier)  # type: ignore[arg-type]
        return self._from_doc(document)

    def get_many(
            self,
            specifications: ISpecification,
            locked: bool = False,
    ) -> list[Aggregate[TId]]:
        visitor = MongoDBEvaluationVisitor()
        specifications.accept(visitor)
        cursor = self._collection.find(visitor.filters, session=self._session)
        return [self._from_doc(doc) for doc in cursor]

    def update(self, aggregate: Aggregate[TId]) -> None:
        result = self._collection.replace_one(
            {"id": aggregate.id},
            asdict(aggregate),
            session=self._session,
        )
        if result.matched_count == 0:
            raise AggregateDoesNotExistError(aggregate)

    def upsert(self, aggregate: Aggregate[TId]) -> None:
        self._collection.replace_one(
            {"id": aggregate.id},
            asdict(aggregate),
            upsert=True,
            session=self._session,
        )
