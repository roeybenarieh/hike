from __future__ import annotations

from typing import Any

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.synchronous.client_session import ClientSession

from hike.ddd.aggregate import Aggregate
from hike.ddd.entity import to_dict, get_fields
from hike.ddd.repository import (
    AggregateAlreadyExistError,
    AggregateDoesNotExistError,
    IRepository,
    TId,
    UnknownError,
)
from hike.ddd.specifications import ISpecification, IVisitor
from hike.ddd.specifications.specs import (
    AndSpecification,
    EqualSpecification,
    GreaterThanEqualSpecification,
    GreaterThanSpecification,
    LessThanEqualSpecification,
    LessThanSpecification,
    NotEqualSpecification,
    NotSpecification,
    OrSpecification,
    BaseLeftRightSpecification,
)
from hike.ddd.uow import DBContext


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


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

    Install with: ``pip install cliff[pymongo]``
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


# ---------------------------------------------------------------------------
# DBContext
# ---------------------------------------------------------------------------


class PyMongoDBContext(DBContext[ClientSession]):
    """DBContext backed by a pymongo ClientSession (ACID transaction).

    Requires a replica set or mongos — standalone MongoDB does not support
    multi-document transactions.

    Usage::

        client = MongoClient("mongodb://localhost:27017")
        ctx = PyMongoDBContext(client)
        with UnitOfWork(repos, ctx):
            repo.add(aggregate)
            uow.commit()
    """

    def __init__(self, client: MongoClient[dict[str, Any]]) -> None:
        super().__init__()
        self._client = client

    def begin(self) -> None:
        self._session = self._client.start_session()
        self._session.start_transaction()

    def commit(self) -> None:
        self.session.commit_transaction()

    def rollback(self) -> None:
        self.session.abort_transaction()

    def close(self) -> None:
        self.session.end_session()
        self._session = None


# ---------------------------------------------------------------------------
# Evaluation visitor
# ---------------------------------------------------------------------------


class MongoDBEvaluationVisitor(IVisitor):
    """Translates a specification tree into a pymongo filter dict.

    Usage::

        visitor = MongoDBEvaluationVisitor()
        spec.accept(visitor)
        documents = collection.find(visitor.filters)

    The filter dict is available as ``visitor.filters`` to pass directly to
    any pymongo collection method.
    """

    def __init__(self) -> None:
        self.filters: dict[str, Any] = {}

    def _pop_filters(self) -> dict[str, Any]:
        f = self.filters
        self.filters = {}
        return f

    def _visit_left_right_spec(
            self, spec: BaseLeftRightSpecification
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        spec.left.accept(self)
        left = self._pop_filters()
        spec.right.accept(self)
        return left, self._pop_filters()

    def visit_and(self, spec: AndSpecification) -> None:
        left, right = self._visit_left_right_spec(spec)
        self.filters = {"$and": [left, right]}

    def visit_or(self, spec: OrSpecification) -> None:
        left, right = self._visit_left_right_spec(spec)
        self.filters = {"$or": [left, right]}

    def visit_not(self, spec: NotSpecification) -> None:
        spec.spec.accept(self)
        inner = self._pop_filters()
        self.filters = {"$nor": [inner]}

    def visit_equal(self, spec: EqualSpecification) -> None:
        self.filters = {spec.field.field_name: {"$eq": spec.operand}}

    def visit_not_equal(self, spec: NotEqualSpecification) -> None:
        self.filters = {spec.field.field_name: {"$ne": spec.operand}}

    def visit_greater_than(self, spec: GreaterThanSpecification) -> None:
        self.filters = {spec.field.field_name: {"$gt": spec.operand}}

    def visit_greater_than_equal(self, spec: GreaterThanEqualSpecification) -> None:
        self.filters = {spec.field.field_name: {"$gte": spec.operand}}

    def visit_less_than(self, spec: LessThanSpecification) -> None:
        self.filters = {spec.field.field_name: {"$lt": spec.operand}}

    def visit_less_than_equal(self, spec: LessThanEqualSpecification) -> None:
        self.filters = {spec.field.field_name: {"$lte": spec.operand}}
