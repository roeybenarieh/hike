from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.synchronous.client_session import ClientSession

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
    LockConflictError,
    ResourceAlreadyExistError,
    ResourceDoesNotExistError,
    IAggregateRepository,
    IRepository,
    OptimisticLockError,
    TAggregate,
    TId,
    TPersistable,
    UnknownError,
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


class PyMongoPersistableRepository(IRepository[TId, TPersistable, ClientSession]):
    """Generic MongoDB repository for any ``Persistable`` object.

    Serialization (``to_dict``) flattens ValueObject fields to their raw
    ``.value``, so the stored document looks like::

        {"id": UUID("…"), "name": "Sea Spirit", "price": 4999.99, "__hike_version": 0}

    Deserialization filters the MongoDB document to the object's
    ``__init__``-eligible fields and unpacks them.  ``_FieldDescriptor.__set__``
    re-wraps raw values into the correct ValueObject type automatically.

    Install with: ``pip install hike[pymongo]``
    """

    def __init__(
            self,
            collection: Collection[dict[str, Any]],
            aggregate_class: type[TPersistable],
            *,
            locks_collection: Collection[dict[str, Any]] | None = None,
            lock_ttl: float = 300.0,
    ) -> None:
        super().__init__()
        self._collection = collection
        self._aggregate_class = aggregate_class
        self._lock_ttl = lock_ttl
        if locks_collection is None:
            db = collection.database
            self._locks_collection: Collection[dict[str, Any]] = db[f"{collection.name}__hike_locks"]
        else:
            self._locks_collection = locks_collection
        self._locks_collection.create_index("obj_id", unique=True, background=True)
        # TTL index: MongoDB auto-deletes documents when expires_at <= now.
        # sparse=True excludes documents with no expires_at (indefinite locks).
        self._locks_collection.create_index(
            [("expires_at", ASCENDING)], expireAfterSeconds=0, sparse=True, background=True
        )

    def _from_doc(self, document: dict[str, Any]) -> TPersistable:
        """Reconstruct an object from a MongoDB document."""
        obj: TPersistable = self._aggregate_class.from_dict(document)  # type: ignore[return-value]
        obj.set_version(document.get("__hike_version", 0))
        return obj

    @staticmethod
    def _id_filter(obj: TPersistable) -> dict[str, Any]:
        return {"id": obj.get_id()}

    def save(self, obj: TPersistable) -> TId:
        doc = {**obj.to_dict(), "__hike_version": 0}
        try:
            result = self._collection.insert_one(doc, session=self._session)
        except Exception as exc:
            raise ResourceAlreadyExistError(obj) from exc
        if not result.acknowledged:
            raise UnknownError("insert_one not acknowledged")
        obj.set_version(0)
        self._after_mutate(obj)
        return obj.get_id()  # pyright: ignore[reportReturnType]

    def _delete(self, identifier: TId) -> None:
        result = self._collection.delete_one(
            {"id": identifier},
            session=self._session,
        )
        if result.deleted_count == 0:
            raise ResourceDoesNotExistError(identifier)

    def get_one(self, identifier: TId) -> TPersistable:
        document = self._collection.find_one({"id": identifier}, session=self._session)
        if document is None:
            raise ResourceDoesNotExistError(identifier)
        return self._from_doc(document)

    def _get_many(
        self,
        specification: ISpecification,
        *,
        ordering: Sequence[OrderBy] | None = None,
        pagination: Pagination | None = None,
    ) -> list[TPersistable] | Page[TPersistable]:
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

    def is_modified(self, obj: TPersistable) -> bool:
        doc = self._collection.find_one(
            {"id": obj.get_id()},
            {"__hike_version": 1},
            session=self._session,
        )
        if doc is None:
            raise ResourceDoesNotExistError(obj)
        return doc.get("__hike_version", 0) != obj.get_version()

    def update(self, obj: TPersistable) -> None:
        v = obj.get_version()
        new_doc = {**obj.to_dict(), "__hike_version": v + 1}
        result = self._collection.replace_one(
            {"id": obj.get_id(), "__hike_version": v},
            new_doc,
            session=self._session,
        )
        if result.matched_count == 0:
            if self._collection.find_one({"id": obj.get_id()}, session=self._session) is None:
                raise ResourceDoesNotExistError(obj)
            raise OptimisticLockError(obj)
        obj.set_version(v + 1)
        self._after_mutate(obj)

    def count(self, specification: ISpecification) -> int:
        visitor = MongoDBEvaluationSpecificationVisitor()
        specification.accept(visitor)
        return self._collection.count_documents(visitor.filters, session=self._session)

    def upsert(self, obj: TPersistable) -> None:
        existing = self._collection.find_one({"id": obj.get_id()}, session=self._session)
        new_version = (existing["__hike_version"] + 1) if existing is not None else 0
        new_doc = {**obj.to_dict(), "__hike_version": new_version}
        self._collection.replace_one(
            {"id": obj.get_id()},
            new_doc,
            upsert=True,
            session=self._session,
        )
        self._after_mutate(obj)

    def watch(self, *, include_existing: bool = False) -> Iterator[TPersistable]:
        pipeline = [{"$match": {"operationType": "insert"}}]
        with self._collection.watch(pipeline, max_await_time_ms=500) as stream:
            seen: set[Any] = set()
            if include_existing:
                for doc in self._collection.find({}):
                    seen.add(doc.get("id"))
                    yield self._from_doc(doc)
            while True:
                change = stream.try_next()
                if change is None:
                    continue
                doc = change.get("fullDocument")
                if doc is None:
                    continue
                doc_id = doc.get("id")
                if doc_id in seen:
                    seen.discard(doc_id)
                    continue
                yield self._from_doc(doc)


    def acquire_lock(self, id: TId, *, owner: Any = None, timeout: float | None = None) -> None:
        owner_str = str(owner) if owner is not None else "__anon__"
        deadline = time.monotonic() + timeout if timeout is not None else None
        expires_dt = datetime.now(timezone.utc) + timedelta(seconds=self._lock_ttl)  # always set
        sleep = 0.05
        while True:
            now_dt = datetime.now(timezone.utc)
            # Eagerly remove any expired lock for this id so we don't have to wait
            # for MongoDB's background TTL worker (which runs every ~60 s).
            self._locks_collection.delete_many({
                "obj_id": id,
                "expires_at": {"$ne": None, "$lte": now_dt},
            })
            existing = self._locks_collection.find_one({"obj_id": id})
            if existing is None:
                try:
                    self._locks_collection.insert_one({"obj_id": id, "owner": owner_str, "expires_at": expires_dt})
                    return
                except Exception:
                    pass  # race — another writer won; retry
            elif existing["owner"] == owner_str:
                # Reentrant — refresh TTL
                self._locks_collection.update_one({"obj_id": id}, {"$set": {"expires_at": expires_dt}})
                return
            if deadline is not None and time.monotonic() >= deadline:
                raise LockConflictError(id, timeout)
            time.sleep(min(sleep, (deadline - time.monotonic()) if deadline is not None else sleep))

    def release_lock(self, id: TId, *, owner: Any = None) -> None:
        owner_str = str(owner) if owner is not None else "__anon__"
        self._locks_collection.delete_one({"obj_id": id, "owner": owner_str})


class PyMongoRepository(
    IAggregateRepository[TId, TAggregate, ClientSession],
    PyMongoPersistableRepository[TId, TAggregate],
):
    """``IAggregateRepository`` backed by MongoDB.

    Extends ``PyMongoPersistableRepository`` with domain-event collection.
    """
