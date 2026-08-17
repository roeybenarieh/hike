from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.synchronous.client_session import ClientSession

from hike.entity import EntityID, from_dict, to_dict
from hike.persistence.ordering import OrderBy
from hike.persistence.pagination import (
    OffsetPagination,
    Page,
    PagePagination,
    Pagination,
    decode_cursor,
    encode_cursor,
)
from hike.persistence.repository import (
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
from hike.specifications import ISpecification

from .visitor import MongoDBEvaluationSpecificationVisitor


def _build_mongo_keyset_filter(
    ordering: list[OrderBy],
    cursor_values: dict[str, Any],
    cursor_id: Any,
) -> dict[str, Any]:
    """Build a MongoDB ``$or`` keyset filter for cursor pagination.

    For ordering ``[price ASC, name DESC]`` with cursor values ``(v1, v2, vid)``:

        {"$or": [
            {"price": {"$gt": v1}},
            {"price": {"$eq": v1}, "name": {"$lt": v2}},
            {"price": {"$eq": v1}, "name": {"$eq": v2}, "id": {"$gt": vid}},
        ]}
    """
    clauses: list[dict[str, Any]] = []
    for i, ob_i in enumerate(ordering):
        path_i = ".".join(ob_i.field.path)
        val_i = cursor_values[path_i]
        prefix: dict[str, Any] = {
            ".".join(ordering[j].field.path): {"$eq": cursor_values[".".join(ordering[j].field.path)]}
            for j in range(i)
        }
        op = "$gt" if ob_i.direction == "asc" else "$lt"
        clauses.append({**prefix, path_i: {op: val_i}})

    # Final clause: all ordering fields equal AND id > cursor_id
    all_eq: dict[str, Any] = {
        ".".join(ob.field.path): {"$eq": cursor_values[".".join(ob.field.path)]}
        for ob in ordering
    }
    clauses.append({**all_eq, "id": {"$gt": cursor_id}})
    return {"$or": clauses}


def _get_doc_value(doc: dict[str, Any], path: list[str]) -> Any:
    """Walk *path* on a MongoDB document dict, traversing nested dicts."""
    val: Any = doc
    for name in path:
        val = val[name]
    return val


class PyMongoRepository(IRepository[TId, ClientSession, TAggregate]):
    """Generic MongoDB repository with optimistic concurrency control.

    Serialization (``to_dict``) flattens ValueObject fields to their raw
    ``.value``, so the stored document looks like::

        {"id": UUID("…"), "name": "Sea Spirit", "price": 4999.99, "__hike_version": 0}

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
        set_version(aggregate, document.get("__hike_version", 0))
        return aggregate

    @staticmethod
    def _id_filter(aggregate: TAggregate) -> dict[str, Any]:
        return {"id": aggregate.id.value}

    def save(self, aggregate: TAggregate) -> TId:
        doc = {**to_dict(aggregate), "__hike_version": 0}
        try:
            result = self._collection.insert_one(doc, session=self._session)
        except Exception as exc:
            raise AggregateAlreadyExistError(aggregate) from exc
        if not result.acknowledged:
            raise UnknownError("insert_one not acknowledged")
        set_version(aggregate, 0)
        self._collect_events(aggregate)
        return aggregate.id  # pyright: ignore[reportReturnType]

    def _delete(self, identifier: EntityID[TId]) -> None:
        result = self._collection.delete_one(
            {"id": identifier.value},
            session=self._session,
        )
        if result.deleted_count == 0:
            raise AggregateDoesNotExistError(identifier)

    def get_one(self, identifier: EntityID[TId]) -> TAggregate:
        document = self._collection.find_one({"id": identifier.value}, session=self._session)
        if document is None:
            raise AggregateDoesNotExistError(identifier)
        return self._from_doc(document)

    def _get_many(
        self,
        specification: ISpecification,
        *,
        ordering: Sequence[OrderBy] | None = None,
        pagination: Pagination | None = None,
    ) -> list[TAggregate] | Page[TAggregate]:
        visitor = MongoDBEvaluationSpecificationVisitor()
        specification.accept(visitor)
        base_filter = visitor.filters

        ordering_list = list(ordering) if ordering else []
        sort_spec = [
            (".".join(ob.field.path), ASCENDING if ob.direction == "asc" else DESCENDING)
            for ob in ordering_list
        ]

        if pagination is None:
            cur = self._collection.find(base_filter, session=self._session)
            if sort_spec:
                cur = cur.sort(sort_spec)
            return [self._from_doc(doc) for doc in cur]

        if isinstance(pagination, OffsetPagination):
            total = self._collection.count_documents(base_filter, session=self._session)
            cur = self._collection.find(base_filter, session=self._session)
            if sort_spec:
                cur = cur.sort(sort_spec)
            cur = cur.skip(pagination.offset).limit(pagination.limit)
            return Page(
                items=[self._from_doc(doc) for doc in cur],
                total=total,
                has_next=(pagination.offset + pagination.limit) < total,
            )

        if isinstance(pagination, PagePagination):
            total = self._collection.count_documents(base_filter, session=self._session)
            offset = pagination.offset
            cur = self._collection.find(base_filter, session=self._session)
            if sort_spec:
                cur = cur.sort(sort_spec)
            cur = cur.skip(offset).limit(pagination.page_size)
            return Page(
                items=[self._from_doc(doc) for doc in cur],
                total=total,
                has_next=(offset + pagination.page_size) < total,
            )

        # CursorPagination — keyset $or filter + implicit id sort + fetch limit+1
        sort_spec_with_id = sort_spec + [("id", ASCENDING)]
        query_filter: dict[str, Any] = dict(base_filter)
        if pagination.cursor is not None:
            cursor_values, cursor_id = decode_cursor(pagination.cursor)
            keyset = _build_mongo_keyset_filter(ordering_list, cursor_values, cursor_id)
            query_filter = {"$and": [base_filter, keyset]} if base_filter else keyset

        cur = self._collection.find(query_filter, session=self._session)
        if sort_spec_with_id:
            cur = cur.sort(sort_spec_with_id)
        docs = list(cur.limit(pagination.limit + 1))

        has_next = len(docs) > pagination.limit
        page_docs = docs[: pagination.limit]
        page_items = [self._from_doc(doc) for doc in page_docs]

        next_cursor: str | None = None
        if has_next and page_docs:
            last_doc = page_docs[-1]
            field_values = {
                ".".join(ob.field.path): _get_doc_value(last_doc, ob.field.path)
                for ob in ordering_list
            }
            next_cursor = encode_cursor(field_values, last_doc["id"])

        return Page(items=page_items, total=None, has_next=has_next, next_cursor=next_cursor)

    def update(self, aggregate: TAggregate) -> None:
        v = get_version(aggregate)
        new_doc = {**to_dict(aggregate), "__hike_version": v + 1}
        result = self._collection.replace_one(
            {"id": aggregate.id.value, "__hike_version": v},
            new_doc,
            session=self._session,
        )
        if result.matched_count == 0:
            if self._collection.find_one({"id": aggregate.id.value}, session=self._session) is None:
                raise AggregateDoesNotExistError(aggregate)
            raise OptimisticLockError(aggregate)
        set_version(aggregate, v + 1)
        self._collect_events(aggregate)

    def count(self, specification: ISpecification) -> int:
        visitor = MongoDBEvaluationSpecificationVisitor()
        specification.accept(visitor)
        return self._collection.count_documents(visitor.filters, session=self._session)

    def upsert(self, aggregate: TAggregate) -> None:
        existing = self._collection.find_one({"id": aggregate.id.value}, session=self._session)
        new_version = (existing["__hike_version"] + 1) if existing is not None else 0
        new_doc = {**to_dict(aggregate), "__hike_version": new_version}
        self._collection.replace_one(
            {"id": aggregate.id.value},
            new_doc,
            upsert=True,
            session=self._session,
        )
        self._collect_events(aggregate)
