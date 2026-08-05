from __future__ import annotations

import json
import uuid as _uuid_mod
from collections.abc import Iterator
from typing import Any, cast

from redis import Redis
from redis.client import Pipeline
from redis.lock import Lock as RedisLock

from hike.ddd.entity import EntityID, get_fields, to_dict
from hike.ddd.repository import (
    AggregateAlreadyExistError,
    AggregateDoesNotExistError,
    IRepository,
    LockTimeoutError,
    TAggregate,
    TId,
)
from hike.ddd.specifications import ISpecification


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


class RedisRepository(IRepository[TId, Pipeline, TAggregate]):
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

    Install with: ``pip install hike[redis]``
    """

    def __init__(
            self,
            client: Redis,  # redis-py stubs pre-parameterize Redis
            aggregate_class: type[TAggregate],
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

    def _serialize(self, aggregate: TAggregate) -> str:
        return json.dumps(to_dict(aggregate), cls=_AggregateEncoder)

    def _deserialize(self, raw: bytes | str) -> TAggregate:
        data: dict[str, Any] = json.loads(raw, object_hook=_aggregate_object_hook)
        init_names = {f.name for f in get_fields(self._aggregate_class) if f.init}
        filtered = {k: v for k, v in data.items() if k in init_names}
        return self._aggregate_class(**filtered)

    def save(self, aggregate: TAggregate) -> TId:
        key = self._key(aggregate.id.value)
        if self._client.exists(key):
            raise AggregateAlreadyExistError(aggregate)
        self.session.set(key, self._serialize(aggregate))
        return aggregate.id  # pyright: ignore[reportReturnType]

    def delete(self, aggregate: TAggregate) -> None:
        key = self._key(aggregate.id.value)
        if not self._client.exists(key):
            raise AggregateDoesNotExistError(aggregate)
        self.session.delete(key)

    def get_one(self, identifier: EntityID[TId], locked: bool = False) -> TAggregate:
        key = self._key(identifier.value)
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
    ) -> list[TAggregate]:
        result: list[TAggregate] = []
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

    def update(self, aggregate: TAggregate) -> None:
        key = self._key(aggregate.id.value)
        if not self._client.exists(key):
            raise AggregateDoesNotExistError(aggregate)
        self.session.set(key, self._serialize(aggregate))

    def upsert(self, aggregate: TAggregate) -> None:
        self.session.set(self._key(aggregate.id.value), self._serialize(aggregate))
