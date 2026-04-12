from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from pymongo.collection import Collection
from pymongo.synchronous.client_session import ClientSession

from cliff.ddd.aggregate import Aggregate
from cliff.ddd.common import DomainError
from cliff.ddd.entity import to_dict, get_fields
from cliff.ddd.specifications import ISpecification
from cliff.ddd.specifications.evaluation_visitors import MongoDBEvaluationVisitor

TId = TypeVar("TId")
TSession = TypeVar("TSession")


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
        return aggregate.id  # type: ignore[return-value]

    def delete(self, aggregate: Aggregate[TId]) -> None:
        key = aggregate.id.value
        if key not in self.session:
            raise AggregateDoesNotExistError(aggregate)
        del self.session[key]

    def get_one(self, identifier: TId, locked: bool = False) -> Aggregate[TId]:
        aggregate = self.session.get(identifier)
        if aggregate is None:
            raise AggregateDoesNotExistError(identifier)  # type: ignore[arg-type]
        return aggregate  # type: ignore[return-value]


    def get_many(
            self,
            specification: ISpecification,
            locked: bool = False,
    ) -> list[Aggregate[TId]]:
        return [agg for agg in self.session.values() if specification.is_satisfied(agg)]  # type: ignore[return-value]

    def update(self, aggregate: Aggregate[TId]) -> None:
        key = aggregate.id.value
        if key not in self.session:
            raise AggregateDoesNotExistError(aggregate)
        self.session[key] = aggregate

    def upsert(self, aggregate: Aggregate[TId]) -> None:
        self.session[aggregate.id.value] = aggregate


class PyMongoRepository(IRepository[TId, ClientSession]):
    """Generic MongoDB repository.

    Serialization (``to_dict``) flattens ValueObject fields to their raw
    ``.value``, so the stored document looks like::

        {"id": UUID("…"), "name": "Sea Spirit", "price": 4999.99}

    Deserialization filters the MongoDB document to the aggregate's
    ``__init__``-eligible fields and unpacks them.  ``_FieldDescriptor.__set__``
    re-wraps raw values into the correct ValueObject type automatically.

    ``locked`` is accepted for interface compatibility but ignored — MongoDB
    does not support row-level pessimistic locking.  Use optimistic concurrency
    (version fields) if you need conflict detection.
    """

    def __init__(
            self,
            collection: Collection[dict[str, Any]],
            aggregate_class: type[Aggregate[TId]],
    ) -> None:
        super().__init__()
        self._collection = collection
        self._aggregate_class = aggregate_class

    def _from_doc(self, document: dict[str, Any]) -> Aggregate[TId]:
        """Reconstruct an aggregate from a MongoDB document.

        Filters the document to only the fields accepted by ``__init__``
        (excludes ``_id`` and any ``init=False`` dataclass fields such as
        ``_events``), then unpacks into ``aggregate_class(**…)``.
        """
        init_field_names = {f.name for f in get_fields(self._aggregate_class) if f.init}
        doc = {k: v for k, v in document.items() if k in init_field_names}
        return self._aggregate_class(**doc)  # type: ignore[return-value]

    @staticmethod
    def _id_filter(aggregate: Aggregate[TId]) -> dict[str, TId]:
        """Return a pymongo filter that matches this aggregate by its flat ``id``."""
        return {"id": aggregate.id.value}

    # ------------------------------------------------------------------
    # IRepository implementation
    # ------------------------------------------------------------------

    def save(self, aggregate: Aggregate[TId]) -> TId:
        doc = to_dict(aggregate)
        try:
            result = self._collection.insert_one(doc, session=self._session)
        except Exception as exc:
            raise AggregateAlreadyExistError(aggregate) from exc
        if not result.acknowledged:
            raise UnknownError("insert_one not acknowledged")
        return aggregate.id  # pyright: ignore[reportReturnType]

    def delete(self, aggregate: Aggregate[TId]) -> None:
        result = self._collection.delete_one(
            self._id_filter(aggregate),
            session=self._session,
        )
        if result.deleted_count == 0:
            raise AggregateDoesNotExistError(aggregate)

    def get_one(self, identifier: TId, locked: bool = False) -> Aggregate[TId]:
        document = self._collection.find_one({"id": identifier}, session=self._session)
        if document is None:
            raise AggregateDoesNotExistError(identifier)  # type: ignore[arg-type]
        return self._from_doc(document)

    def get_many(
            self,
            specification: ISpecification,
            locked: bool = False,
    ) -> list[Aggregate[TId]]:
        visitor = MongoDBEvaluationVisitor()
        specification.accept(visitor)
        cursor = self._collection.find(visitor.filters, session=self._session)
        return [self._from_doc(doc) for doc in cursor]

    def update(self, aggregate: Aggregate[TId]) -> None:
        result = self._collection.replace_one(
            self._id_filter(aggregate),
            to_dict(aggregate),
            session=self._session,
        )
        if result.matched_count == 0:
            raise AggregateDoesNotExistError(aggregate)

    def upsert(self, aggregate: Aggregate[TId]) -> None:
        self._collection.replace_one(
            self._id_filter(aggregate),
            to_dict(aggregate),
            upsert=True,
            session=self._session,
        )