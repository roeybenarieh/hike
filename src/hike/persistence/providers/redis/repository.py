from __future__ import annotations

import json
import uuid as _uuid_mod
from collections.abc import Iterator, Sequence
from typing import Any, cast

from redis import Redis
from redis.client import Pipeline

from hike.entity import EntityID, from_dict, to_dict
from hike.persistence.ordering import OrderBy, apply_ordering_in_memory, get_field_value
from hike.persistence.pagination import (
    OffsetPagination,
    Page,
    PagePagination,
    Pagination,
    cursor_position,
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
    get_version,
    set_version,
)
from hike.specifications import ISpecification


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


class RedisRepository(IRepository[TId, TAggregate, Pipeline]):
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
        data["__hike_version"] = version
        return json.dumps(data, cls=_AggregateEncoder)

    def _deserialize(self, raw: bytes | str) -> TAggregate:
        data: dict[str, Any] = json.loads(raw, object_hook=_aggregate_object_hook)
        version: int = data.pop("__hike_version", 0)
        aggregate: TAggregate = from_dict(self._aggregate_class, data)
        set_version(aggregate, version)
        return aggregate

    def save(self, aggregate: TAggregate) -> TId:
        key = self._key(aggregate.id.value)
        if self._client.exists(key):
            raise AggregateAlreadyExistError(aggregate)
        self.session.set(key, self._serialize(aggregate, version=0))
        set_version(aggregate, 0)
        self._collect_events(aggregate)
        return aggregate.id  # pyright: ignore[reportReturnType]

    def _delete(self, identifier: EntityID[TId]) -> None:
        key = self._key(identifier.value)
        if not self._client.exists(key):
            raise AggregateDoesNotExistError(identifier)
        self.session.delete(key)

    def get_one(self, identifier: EntityID[TId]) -> TAggregate:
        key = self._key(identifier.value)
        raw = cast(bytes | None, self._client.get(key))
        if raw is None:
            raise AggregateDoesNotExistError(identifier)
        return self._deserialize(raw)

    def _fetch_matching(self, specification: ISpecification) -> list[TAggregate]:
        """Scan all keys and return aggregates matching *specification*."""
        result: list[TAggregate] = []
        for key in cast(Iterator[bytes], self._client.scan_iter(f"{self._key_prefix}:*")):  # pyright: ignore[reportUnknownMemberType]
            raw = cast(bytes | None, self._client.get(key))
            if raw is None:
                continue
            aggregate = self._deserialize(raw)
            if specification.is_satisfied(aggregate):
                result.append(aggregate)
        return result

    def _get_many(
        self,
        specification: ISpecification,
        *,
        ordering: Sequence[OrderBy] | None = None,
        pagination: Pagination | None = None,
    ) -> list[TAggregate] | Page[TAggregate]:
        matched = self._fetch_matching(specification)

        ordering_list = list(ordering) if ordering else []
        if ordering_list:
            matched = apply_ordering_in_memory(matched, ordering_list)

        if pagination is None:
            return matched

        if isinstance(pagination, OffsetPagination):
            total = len(matched)
            page_items = matched[pagination.offset : pagination.offset + pagination.limit]
            return Page(
                items=page_items,
                total=total,
                has_next=(pagination.offset + pagination.limit) < total,
            )

        if isinstance(pagination, PagePagination):
            total = len(matched)
            offset = pagination.offset
            page_items = matched[offset : offset + pagination.page_size]
            return Page(
                items=page_items,
                total=total,
                has_next=(offset + pagination.page_size) < total,
            )

        # CursorPagination — same keyset logic as InMemory (all in-memory anyway)
        matched = apply_ordering_in_memory(matched, ordering_list, id_tiebreaker=True)
        start = 0
        if pagination.cursor is not None:
            cursor_values, cursor_id = decode_cursor(pagination.cursor)
            start = cursor_position(matched, cursor_values, cursor_id, ordering_list)

        page_items = matched[start : start + pagination.limit]
        has_next = (start + pagination.limit) < len(matched)
        next_cursor: str | None = None
        if page_items and has_next:
            last = page_items[-1]
            field_values = {
                ".".join(ob.field.path): get_field_value(last, ob.field.path)
                for ob in ordering_list
            }
            next_cursor = encode_cursor(field_values, last.id.value)

        return Page(items=page_items, total=None, has_next=has_next, next_cursor=next_cursor)

    def update(self, aggregate: TAggregate) -> None:
        key = self._key(aggregate.id.value)
        raw = cast(bytes | None, self._client.get(key))
        if raw is None:
            raise AggregateDoesNotExistError(aggregate)
        current_version: int = json.loads(raw, object_hook=_aggregate_object_hook).get("__hike_version", 0)
        v = get_version(aggregate)
        if current_version != v:
            raise OptimisticLockError(aggregate)
        self.session.set(key, self._serialize(aggregate, version=v + 1))
        set_version(aggregate, v + 1)
        self._collect_events(aggregate)

    def count(self, specification: ISpecification) -> int:
        total = 0
        for key in cast(Iterator[bytes], self._client.scan_iter(f"{self._key_prefix}:*")):  # pyright: ignore[reportUnknownMemberType]
            raw = cast(bytes | None, self._client.get(key))
            if raw is not None and specification.is_satisfied(self._deserialize(raw)):
                total += 1
        return total

    def upsert(self, aggregate: TAggregate) -> None:
        key = self._key(aggregate.id.value)
        raw = cast(bytes | None, self._client.get(key))
        if raw is not None:
            current_version: int = json.loads(raw, object_hook=_aggregate_object_hook).get("__hike_version", 0)
            new_version = current_version + 1
        else:
            new_version = 0
        self.session.set(key, self._serialize(aggregate, version=new_version))
        self._collect_events(aggregate)
