from __future__ import annotations

from typing import Any

from pymongo.collection import Collection
from pymongo.synchronous.client_session import ClientSession

from hike.ddd.aggregate import Aggregate
from hike.ddd.entity import get_fields, to_dict
from hike.ddd.repository import (
    AggregateAlreadyExistError,
    AggregateDoesNotExistError,
    IRepository,
    TId,
    UnknownError,
)
from hike.ddd.specifications import ISpecification

from .visitor import MongoDBEvaluationVisitor


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

    Install with: ``pip install hike[pymongo]``
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
        """Reconstruct an aggregate from a MongoDB document."""
        init_field_names = {f.name for f in get_fields(self._aggregate_class) if f.init}
        doc = {k: v for k, v in document.items() if k in init_field_names}
        return self._aggregate_class(**doc)

    @staticmethod
    def _id_filter(aggregate: Aggregate[TId]) -> dict[str, TId]:
        return {"id": aggregate.id.value}

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
            raise AggregateDoesNotExistError(identifier)
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
