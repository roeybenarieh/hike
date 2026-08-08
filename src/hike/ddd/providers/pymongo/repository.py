from __future__ import annotations

from typing import Any

from pymongo.collection import Collection
from pymongo.synchronous.client_session import ClientSession

from hike.ddd.entity import EntityID, from_dict, to_dict
from hike.ddd.repository import (
    AggregateAlreadyExistError,
    AggregateDoesNotExistError,
    IRepository,
    OptimisticLockError,
    TAggregate,
    TId,
    UnknownError,
    get_version,
    set_version,
)
from hike.ddd.specifications import ISpecification

from .visitor import MongoDBEvaluationSpecificationVisitor


class PyMongoRepository(IRepository[TId, ClientSession, TAggregate]):
    """Generic MongoDB repository with optimistic concurrency control.

    Serialization (``to_dict``) flattens ValueObject fields to their raw
    ``.value``, so the stored document looks like::

        {"id": UUID("…"), "name": "Sea Spirit", "price": 4999.99, "_version": 0}

    Deserialization filters the MongoDB document to the aggregate's
    ``__init__``-eligible fields and unpacks them.  ``_FieldDescriptor.__set__``
    re-wraps raw values into the correct ValueObject type automatically.

    Each ``update`` includes the current ``aggregate.version`` in the filter.
    If the stored version has advanced (another writer committed), MongoDB
    matches nothing and ``OptimisticLockError`` is raised.  On success,
    ``aggregate.version`` is incremented to match the newly stored value.

    Install with: ``pip install hike[pymongo]``
    """

    def __init__(
            self,
            collection: Collection[dict[str, Any]],
            aggregate_class: type[TAggregate],
    ) -> None:
        super().__init__()
        self._collection = collection
        self._aggregate_class = aggregate_class

    def _from_doc(self, document: dict[str, Any]) -> TAggregate:
        """Reconstruct an aggregate from a MongoDB document."""
        aggregate: TAggregate = from_dict(self._aggregate_class, document)
        set_version(aggregate, document.get("_version", 0))
        return aggregate

    @staticmethod
    def _id_filter(aggregate: TAggregate) -> dict[str, Any]:
        return {"id": aggregate.id.value}

    def save(self, aggregate: TAggregate) -> TId:
        doc = {**to_dict(aggregate), "_version": 0}
        try:
            result = self._collection.insert_one(doc, session=self._session)
        except Exception as exc:
            raise AggregateAlreadyExistError(aggregate) from exc
        if not result.acknowledged:
            raise UnknownError("insert_one not acknowledged")
        set_version(aggregate, 0)
        return aggregate.id  # pyright: ignore[reportReturnType]

    def delete(self, aggregate: TAggregate) -> None:
        result = self._collection.delete_one(
            {"id": aggregate.id.value, "_version": get_version(aggregate)},
            session=self._session,
        )
        if result.deleted_count == 0:
            if self._collection.find_one({"id": aggregate.id.value}, session=self._session) is None:
                raise AggregateDoesNotExistError(aggregate)
            raise OptimisticLockError(aggregate)

    def get_one(self, identifier: EntityID[TId]) -> TAggregate:
        document = self._collection.find_one({"id": identifier.value}, session=self._session)
        if document is None:
            raise AggregateDoesNotExistError(identifier)
        return self._from_doc(document)

    def get_many(self, specification: ISpecification) -> list[TAggregate]:
        visitor = MongoDBEvaluationSpecificationVisitor()
        specification.accept(visitor)
        cursor = self._collection.find(visitor.filters, session=self._session)
        return [self._from_doc(doc) for doc in cursor]

    def update(self, aggregate: TAggregate) -> None:
        v = get_version(aggregate)
        new_doc = {**to_dict(aggregate), "_version": v + 1}
        result = self._collection.replace_one(
            {"id": aggregate.id.value, "_version": v},
            new_doc,
            session=self._session,
        )
        if result.matched_count == 0:
            if self._collection.find_one({"id": aggregate.id.value}, session=self._session) is None:
                raise AggregateDoesNotExistError(aggregate)
            raise OptimisticLockError(aggregate)
        set_version(aggregate, v + 1)

    def upsert(self, aggregate: TAggregate) -> None:
        existing = self._collection.find_one({"id": aggregate.id.value}, session=self._session)
        new_version = (existing["_version"] + 1) if existing is not None else 0
        new_doc = {**to_dict(aggregate), "_version": new_version}
        self._collection.replace_one(
            {"id": aggregate.id.value},
            new_doc,
            upsert=True,
            session=self._session,
        )
