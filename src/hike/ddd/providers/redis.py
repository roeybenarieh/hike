from __future__ import annotations

import json
import uuid as _uuid_mod
from collections.abc import Iterator
from typing import Any, cast

from redis import Redis
from redis.client import Pipeline
from redis.lock import Lock as RedisLock

from hike.ddd.aggregate import Aggregate
from hike.ddd.entity import to_dict, get_fields
from hike.ddd.repository import (
    AggregateAlreadyExistError,
    AggregateDoesNotExistError,
    IRepository,
    LockTimeoutError,
    TId,
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
# JSON helpers
# ---------------------------------------------------------------------------


class _AggregateEncoder(json.JSONEncoder):
    """JSON encoder that round-trips ``uuid.UUID`` values via ``{"__uuid__": "…"}``."""

    def default(self, o: object) -> object:
        if isinstance(o, _uuid_mod.UUID):
            return {"__uuid__": str(o)}
        return super().default(o)


def _aggregate_object_hook(obj: dict[str, Any]) -> Any:
    if "__uuid__" in obj:
        return _uuid_mod.UUID(str(obj["__uuid__"]))
    return obj


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


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

    Install with: ``pip install cliff[redis]``
    """

    def __init__(
            self,
            client: Redis,  # redis-py stubs pre-parameterize Redis
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

    @property
    def session(self) -> Pipeline:
        if self._session is None:
            raise RuntimeError("Session wasn't provided to the repository")
        return self._session

    @session.setter
    def session(self, value: Pipeline) -> None:
        self.release_locks()
        self._session = value

    def _acquire_lock(self, key: str) -> None:
        lock = self._client.lock(
            f"lock:{key}",
            timeout=self._lock_timeout,
            blocking_timeout=self._lock_blocking_timeout,
        )
        acquired: bool = lock.acquire()
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

    def _key(self, raw_id: Any) -> str:
        return f"{self._key_prefix}:{raw_id}"

    def _serialize(self, aggregate: Aggregate[TId]) -> str:
        return json.dumps(to_dict(aggregate), cls=_AggregateEncoder)

    def _deserialize(self, raw: bytes | str) -> Aggregate[TId]:
        data: dict[str, Any] = json.loads(raw, object_hook=_aggregate_object_hook)
        init_names = {f.name for f in get_fields(self._aggregate_class) if f.init}
        filtered = {k: v for k, v in data.items() if k in init_names}
        return self._aggregate_class(**filtered)

    def save(self, aggregate: Aggregate[TId]) -> TId:
        key = self._key(aggregate.id.value)
        if self._client.exists(key):
            raise AggregateAlreadyExistError(aggregate)
        self.session.set(key, self._serialize(aggregate))
        return aggregate.id  # pyright: ignore[reportReturnType]

    def delete(self, aggregate: Aggregate[TId]) -> None:
        key = self._key(aggregate.id.value)
        if not self._client.exists(key):
            raise AggregateDoesNotExistError(aggregate)
        self.session.delete(key)

    def get_one(self, identifier: TId, locked: bool = False) -> Aggregate[TId]:
        key = self._key(identifier)
        if locked:
            self._acquire_lock(key)
        raw = cast(bytes | None, self._client.get(key))
        if raw is None:
            raise AggregateDoesNotExistError(identifier)
        return self._deserialize(raw)

    def get_many(
            self,
            specification: ISpecification,
            locked: bool = False,
    ) -> list[Aggregate[TId]]:
        result: list[Aggregate[TId]] = []
        for key in cast(Iterator[bytes], self._client.scan_iter(f"{self._key_prefix}:*")):  # pyright: ignore[reportUnknownMemberType]
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


# ---------------------------------------------------------------------------
# DBContext
# ---------------------------------------------------------------------------


class RedisDBContext(DBContext[Pipeline]):
    """DBContext backed by a redis Pipeline (MULTI/EXEC transaction).

    All commands queued between ``begin()`` and ``commit()`` are sent
    atomically.  ``rollback()`` discards the queued commands (DISCARD).

    Usage::

        client = Redis(host="localhost", port=6379)
        ctx = RedisDBContext(client)
        with UnitOfWork(repos, ctx):
            repo.add(aggregate)
            uow.commit()
    """

    def __init__(self, client: Redis) -> None:
        super().__init__()
        self._client = client

    def begin(self) -> None:
        self._session = self._client.pipeline(transaction=True)  # pyright: ignore[reportUnknownMemberType]

    def commit(self) -> None:
        self.session.execute()

    def rollback(self) -> None:
        self.session.reset()

    def close(self) -> None:
        self.session.reset()
        self._session = None


# ---------------------------------------------------------------------------
# Evaluation visitor
# ---------------------------------------------------------------------------

# RediSearch query syntax quick reference:
#   Numeric range : @field:[min max]      (inclusive bounds)
#                   @field:[(min max]     ( = exclusive lower bound
#   Tag exact match: @field:{value}
#   AND            : expr1 expr2          (space-separated = implicit AND)
#   OR             : (expr1 | expr2)
#   NOT            : -(expr)


def _redis_operand(operand: object) -> str:
    """Format an operand for embedding in a RediSearch query string."""
    if isinstance(operand, str):
        escaped = operand.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        return escaped
    return str(operand)


def _redis_leaf(field_name: str, operand: object, op: str) -> str:
    """Build a RediSearch clause for a single field comparison."""
    if isinstance(operand, (int, float)):
        val = float(operand)
        match op:
            case "eq":
                return f"@{field_name}:[{val} {val}]"
            case "ne":
                return f"-(@{field_name}:[{val} {val}])"
            case "gt":
                return f"@{field_name}:[({val} +inf]"
            case "gte":
                return f"@{field_name}:[{val} +inf]"
            case "lt":
                return f"@{field_name}:[-inf ({val}]"
            case "lte":
                return f"@{field_name}:[-inf {val}]"
            case _:
                raise ValueError(f"Unknown operator: {op!r}")
    else:
        tag = _redis_operand(operand)
        match op:
            case "eq":
                return f"@{field_name}:{{{tag}}}"
            case "ne":
                return f"-(@{field_name}:{{{tag}}})"
            case _:
                raise TypeError(
                    f"RediSearch range operator '{op}' is only supported for "
                    f"numeric fields; got operand {operand!r} of type {type(operand).__name__}"
                )


class RedisEvaluationVisitor(IVisitor):
    """Translates a specification tree into a RediSearch FT.SEARCH query string.

    Usage::

        visitor = RedisEvaluationVisitor(redis_client, index_name="idx:products")
        spec.accept(visitor)
        raw_results = visitor.result()   # calls FT.SEARCH and returns the raw reply

    The query string is also accessible as ``visitor.query`` before calling
    ``result()`` if you need to inspect or log it first.
    """

    _MATCH_ALL = "*"

    def __init__(self, client: Redis, index_name: str) -> None:
        self._client = client
        self._index = index_name
        self.query: str = self._MATCH_ALL

    def result(self) -> Any:
        return self._client.ft(self._index).search(self.query)  # pyright: ignore[reportUnknownVariableType]

    def _pop_query(self) -> str:
        q = self.query
        self.query = self._MATCH_ALL
        return q

    def _visit_left_right_spec(self, spec: BaseLeftRightSpecification) -> tuple[str, str]:
        spec.left.accept(self)
        left = self._pop_query()
        spec.right.accept(self)
        return left, self._pop_query()

    def visit_and(self, spec: AndSpecification) -> None:
        left, right = self._visit_left_right_spec(spec)
        self.query = f"({left}) ({right})"

    def visit_or(self, spec: OrSpecification) -> None:
        left, right = self._visit_left_right_spec(spec)
        self.query = f"({left} | {right})"

    def visit_not(self, spec: NotSpecification) -> None:
        spec.spec.accept(self)
        self.query = f"-({self._pop_query()})"

    def visit_equal(self, spec: EqualSpecification) -> None:
        self.query = _redis_leaf(spec.field.field_name, spec.operand, "eq")

    def visit_not_equal(self, spec: NotEqualSpecification) -> None:
        self.query = _redis_leaf(spec.field.field_name, spec.operand, "ne")

    def visit_greater_than(self, spec: GreaterThanSpecification) -> None:
        self.query = _redis_leaf(spec.field.field_name, spec.operand, "gt")

    def visit_greater_than_equal(self, spec: GreaterThanEqualSpecification) -> None:
        self.query = _redis_leaf(spec.field.field_name, spec.operand, "gte")

    def visit_less_than(self, spec: LessThanSpecification) -> None:
        self.query = _redis_leaf(spec.field.field_name, spec.operand, "lt")

    def visit_less_than_equal(self, spec: LessThanEqualSpecification) -> None:
        self.query = _redis_leaf(spec.field.field_name, spec.operand, "lte")
