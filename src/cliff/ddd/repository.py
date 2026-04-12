import json
import uuid as _uuid_mod
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any, Generic, TypeVar, cast

from pymongo.collection import Collection
from pymongo.synchronous.client_session import ClientSession
from redis import Redis
from redis.client import Pipeline
from redis.lock import Lock as RedisLock
from sqlalchemy.orm import Session

from cliff.ddd.aggregate import Aggregate
from cliff.ddd.common import DomainError
from cliff.ddd.entity import to_dict, get_fields
from cliff.ddd.specifications import ISpecification
from cliff.ddd.specifications.evaluation_visitors import (
    MongoDBEvaluationVisitor,
    SQLAlchemyEvaluationVisitor,
)

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


class LockTimeoutError(RepositoryError):
    """Raised when a pessimistic lock cannot be acquired within the allowed time."""


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


# ---------------------------------------------------------------------------
# SQLAlchemy repository
# ---------------------------------------------------------------------------


class SQLAlchemyRepository(IRepository[TId, Session]):
    """Generic SQLAlchemy ORM repository.

    ``model_class`` must be a SQLAlchemy mapped class whose column names match
    the aggregate's dataclass field names after ``to_dict`` flattening.
    ``get_many`` translates the specification tree into a SQL WHERE clause via
    ``SQLAlchemyEvaluationVisitor``.  ``locked=True`` adds ``FOR UPDATE``.
    """

    def __init__(
            self,
            model_class: type[Any],
            aggregate_class: type[Aggregate[TId]],
    ) -> None:
        super().__init__()
        self._model_class = model_class
        self._aggregate_class = aggregate_class

    def _from_model(self, model: Any) -> Aggregate[TId]:
        init_names = {f.name for f in get_fields(self._aggregate_class) if f.init}
        data = {k: getattr(model, k) for k in init_names if hasattr(model, k)}
        return self._aggregate_class(**data)  # type: ignore[return-value]

    def save(self, aggregate: Aggregate[TId]) -> TId:
        model = self._model_class(**to_dict(aggregate))
        try:
            self.session.add(model)
            self.session.flush()
        except Exception as exc:
            raise AggregateAlreadyExistError(aggregate) from exc
        return aggregate.id  # type: ignore[return-value]

    def delete(self, aggregate: Aggregate[TId]) -> None:
        model = self.session.get(self._model_class, aggregate.id.value)
        if model is None:
            raise AggregateDoesNotExistError(aggregate)
        self.session.delete(model)

    def get_one(self, identifier: TId, locked: bool = False) -> Aggregate[TId]:
        model = self.session.get(
            self._model_class,
            identifier,
            with_for_update=True if locked else None,
        )
        if model is None:
            raise AggregateDoesNotExistError(identifier)  # type: ignore[arg-type]
        return self._from_model(model)

    def get_many(
            self,
            specification: ISpecification,
            locked: bool = False,
    ) -> list[Aggregate[TId]]:
        visitor = SQLAlchemyEvaluationVisitor(self._model_class)  # type: ignore[arg-type]
        specification.accept(visitor)
        stmt = visitor.result()
        if locked:
            stmt = stmt.with_for_update()
        rows = self.session.scalars(stmt).all()
        return [self._from_model(row) for row in rows]  # type: ignore[return-value]

    def update(self, aggregate: Aggregate[TId]) -> None:
        model = self.session.get(self._model_class, aggregate.id.value)
        if model is None:
            raise AggregateDoesNotExistError(aggregate)
        for key, val in to_dict(aggregate).items():
            setattr(model, key, val)

    def upsert(self, aggregate: Aggregate[TId]) -> None:
        model = self.session.get(self._model_class, aggregate.id.value)
        if model is None:
            self.session.add(self._model_class(**to_dict(aggregate)))
        else:
            for key, val in to_dict(aggregate).items():
                setattr(model, key, val)


# ---------------------------------------------------------------------------
# Redis repository
# ---------------------------------------------------------------------------


class _AggregateEncoder(json.JSONEncoder):
    """JSON encoder that round-trips ``uuid.UUID`` values via ``{"__uuid__": "…"}``."""

    def default(self, o: object) -> object:
        if isinstance(o, _uuid_mod.UUID):
            return {"__uuid__": str(o)}
        return super().default(o)  # type: ignore[misc]


def _aggregate_object_hook(obj: dict[str, Any]) -> Any:
    if "__uuid__" in obj:
        return _uuid_mod.UUID(str(obj["__uuid__"]))
    return obj


class RedisRepository(IRepository[TId, Pipeline]):
    """Generic Redis repository with distributed locking via ``redis.lock.Lock``.

    Aggregates are stored as JSON strings under keys ``<key_prefix>:<raw_id>``.
    Write operations (save, delete, update, upsert) are queued into the
    pipeline session so they execute atomically on commit.  Read operations
    (get_one, get_many) bypass the pipeline and go directly to the raw client
    because pipeline commands return no results until executed.

    **Locking** — when ``locked=True`` is passed to ``get_one`` or
    ``get_many``, a ``redis.lock.Lock`` is acquired on each key before the
    value is read (using the lock key ``lock:<data-key>``).  All held locks
    are released by calling ``release_locks()``, which is also called
    automatically when a new pipeline session is assigned (i.e. at the start
    of the next ``UnitOfWork`` block).  Locks expire unconditionally after
    ``lock_timeout`` seconds as a safety net.

    **UUID serialization** — ``uuid.UUID`` values survive JSON round-trips
    via a ``{"__uuid__": "…"}`` envelope.
    """

    def __init__(
            self,
            client: Redis,  # type: ignore[type-arg]  # redis-py stubs pre-parameterize Redis
            aggregate_class: type[Aggregate[TId]],
            key_prefix: str,
            *,
            lock_timeout: float = 30.0,
            lock_blocking_timeout: float | None = 10.0,
    ) -> None:
        super().__init__()
        self._client = client
        self._aggregate_class = aggregate_class
        self._key_prefix = key_prefix
        self._lock_timeout = lock_timeout
        self._lock_blocking_timeout = lock_blocking_timeout
        self._held_locks: dict[str, RedisLock] = {}

    # Full property override — also narrows the return type to Pipeline.
    # Releases any locks left over from a previous session on assignment.
    @property
    def session(self) -> Pipeline:
        if self._session is None:
            raise RuntimeError("Session wasn't provided to the repository")
        return self._session  # type: ignore[return-value]

    @session.setter
    def session(self, value: Pipeline) -> None:
        self.release_locks()
        self._session = value

    # ------------------------------------------------------------------
    # Lock helpers
    # ------------------------------------------------------------------

    def _acquire_lock(self, key: str) -> None:
        """Acquire a distributed lock for *key*; raise ``DBConnectionError`` on failure."""
        lock = self._client.lock(
            f"lock:{key}",
            timeout=self._lock_timeout,
            blocking_timeout=self._lock_blocking_timeout,
        )
        acquired: bool = lock.acquire()  # type: ignore[assignment]
        if not acquired:
            raise LockTimeoutError(
                f"Could not acquire lock for {key!r} within {self._lock_blocking_timeout}s"
            )
        self._held_locks[key] = lock

    def release_locks(self) -> None:
        """Release all locks held by this repository."""
        for lock in self._held_locks.values():
            try:
                lock.release()
            except Exception:
                pass
        self._held_locks.clear()

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def _key(self, raw_id: Any) -> str:
        return f"{self._key_prefix}:{raw_id}"

    def _serialize(self, aggregate: Aggregate[TId]) -> str:
        return json.dumps(to_dict(aggregate), cls=_AggregateEncoder)

    def _deserialize(self, raw: bytes | str) -> Aggregate[TId]:
        data: dict[str, Any] = json.loads(raw, object_hook=_aggregate_object_hook)
        init_names = {f.name for f in get_fields(self._aggregate_class) if f.init}
        filtered = {k: v for k, v in data.items() if k in init_names}
        return self._aggregate_class(**filtered)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # IRepository implementation
    # ------------------------------------------------------------------

    def save(self, aggregate: Aggregate[TId]) -> TId:
        key = self._key(aggregate.id.value)
        if self._client.exists(key):
            raise AggregateAlreadyExistError(aggregate)
        self.session.set(key, self._serialize(aggregate))
        return aggregate.id  # type: ignore[return-value]

    def delete(self, aggregate: Aggregate[TId]) -> None:
        key = self._key(aggregate.id.value)
        if not self._client.exists(key):
            raise AggregateDoesNotExistError(aggregate)
        self.session.delete(key)  # type: ignore[misc]

    def get_one(self, identifier: TId, locked: bool = False) -> Aggregate[TId]:
        key = self._key(identifier)
        if locked:
            self._acquire_lock(key)
        raw = cast(bytes | None, self._client.get(key))
        if raw is None:
            raise AggregateDoesNotExistError(identifier)  # type: ignore[arg-type]
        return self._deserialize(raw)

    def get_many(
            self,
            specification: ISpecification,
            locked: bool = False,
    ) -> list[Aggregate[TId]]:
        result: list[Aggregate[TId]] = []
        for key in cast(Iterator[bytes], self._client.scan_iter(f"{self._key_prefix}:*")):  # type: ignore[reportUnknownMemberType]
            raw = cast(bytes | None, self._client.get(key))
            if raw is None:
                continue
            aggregate = self._deserialize(raw)
            if specification.is_satisfied(aggregate):
                if locked:
                    self._acquire_lock(key.decode())
                result.append(aggregate)
        return result

    def update(self, aggregate: Aggregate[TId]) -> None:
        key = self._key(aggregate.id.value)
        if not self._client.exists(key):
            raise AggregateDoesNotExistError(aggregate)
        self.session.set(key, self._serialize(aggregate))

    def upsert(self, aggregate: Aggregate[TId]) -> None:
        self.session.set(self._key(aggregate.id.value), self._serialize(aggregate))