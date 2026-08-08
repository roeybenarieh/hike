from __future__ import annotations

import json
import uuid as _uuid_mod
from collections.abc import Iterator
from typing import Any, cast

from redis import Redis
from redis.client import Pipeline

from hike.ddd.entity import EntityID, from_dict, to_dict
from hike.ddd.repository import (
    AggregateAlreadyExistError,
    AggregateDoesNotExistError,
    IRepository,
    OptimisticLockError,
    TAggregate,
    TId,
    get_version,
    set_version,
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
    """Generic Redis repository with optimistic concurrency control.

    Aggregates are stored as JSON strings under keys ``<key_prefix>:<raw_id>``.
    Write operations (save, delete, update, upsert) are queued into the
    pipeline session so they execute atomically on commit.  Read operations
    (get_one, get_many) bypass the pipeline and go directly to the raw client
    because pipeline commands return no results until executed.

    **Optimistic locking** — each stored document includes a ``_version``
    integer field.  ``update`` reads the current version directly from Redis,
    compares it against ``aggregate.version``, and raises ``OptimisticLockError``
    on mismatch.  On success the new document is queued to the pipeline with
    ``_version + 1`` and ``aggregate.version`` is incremented accordingly.
    Note: there is a TOCTOU window between the version check (direct read) and
    the pipeline execution (commit); this is consistent with how ``save`` and
    ``delete`` check existence before queuing.

    **UUID serialization** — ``uuid.UUID`` values survive JSON round-trips
    via a ``{"__uuid__": "…"}`` envelope.

    Install with: ``pip install hike[redis]``
    """

    def __init__(
            self,
            client: Redis,  # redis-py stubs pre-parameterize Redis
            aggregate_class: type[TAggregate],
            key_prefix: str,
    ) -> None:
        super().__init__()
        self._client = client
        self._aggregate_class = aggregate_class
        self._key_prefix = key_prefix

    def _key(self, raw_id: Any) -> str:
        return f"{self._key_prefix}:{raw_id}"

    def _serialize(self, aggregate: TAggregate, *, version: int) -> str:
        data = to_dict(aggregate)
        data["_version"] = version
        return json.dumps(data, cls=_AggregateEncoder)

    def _deserialize(self, raw: bytes | str) -> TAggregate:
        data: dict[str, Any] = json.loads(raw, object_hook=_aggregate_object_hook)
        version: int = data.pop("_version", 0)
        aggregate: TAggregate = from_dict(self._aggregate_class, data)
        set_version(aggregate, version)
        return aggregate

    def save(self, aggregate: TAggregate) -> TId:
        key = self._key(aggregate.id.value)
        if self._client.exists(key):
            raise AggregateAlreadyExistError(aggregate)
        self.session.set(key, self._serialize(aggregate, version=0))
        set_version(aggregate, 0)
        return aggregate.id  # pyright: ignore[reportReturnType]

    def delete(self, aggregate: TAggregate) -> None:
        key = self._key(aggregate.id.value)
        raw = cast(bytes | None, self._client.get(key))
        if raw is None:
            raise AggregateDoesNotExistError(aggregate)
        current_version: int = json.loads(raw, object_hook=_aggregate_object_hook).get("_version", 0)
        if current_version != get_version(aggregate):
            raise OptimisticLockError(aggregate)
        self.session.delete(key)

    def get_one(self, identifier: EntityID[TId]) -> TAggregate:
        key = self._key(identifier.value)
        raw = cast(bytes | None, self._client.get(key))
        if raw is None:
            raise AggregateDoesNotExistError(identifier)
        return self._deserialize(raw)

    def get_many(self, specification: ISpecification) -> list[TAggregate]:
        result: list[TAggregate] = []
        for key in cast(Iterator[bytes], self._client.scan_iter(f"{self._key_prefix}:*")):  # pyright: ignore[reportUnknownMemberType]
            raw = cast(bytes | None, self._client.get(key))
            if raw is None:
                continue
            aggregate = self._deserialize(raw)
            if specification.is_satisfied(aggregate):
                result.append(aggregate)
        return result

    def update(self, aggregate: TAggregate) -> None:
        key = self._key(aggregate.id.value)
        raw = cast(bytes | None, self._client.get(key))
        if raw is None:
            raise AggregateDoesNotExistError(aggregate)
        current_version: int = json.loads(raw, object_hook=_aggregate_object_hook).get("_version", 0)
        v = get_version(aggregate)
        if current_version != v:
            raise OptimisticLockError(aggregate)
        self.session.set(key, self._serialize(aggregate, version=v + 1))
        set_version(aggregate, v + 1)

    def upsert(self, aggregate: TAggregate) -> None:
        key = self._key(aggregate.id.value)
        raw = cast(bytes | None, self._client.get(key))
        if raw is not None:
            current_version: int = json.loads(raw, object_hook=_aggregate_object_hook).get("_version", 0)
            new_version = current_version + 1
        else:
            new_version = 0
        self.session.set(key, self._serialize(aggregate, version=new_version))
