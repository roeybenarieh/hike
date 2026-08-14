"""Pagination and ordering primitives for ``IRepository.get_many``."""
from __future__ import annotations

import base64
import json
import uuid as _uuid_mod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar, cast

from hike.entity import TerminalFieldProxy
from hike.value_object import ValueObject

TItem = TypeVar("TItem")


@dataclass(frozen=True)
class OrderBy:
    """Order results by *field* in the given *direction*."""

    field: TerminalFieldProxy
    direction: Literal["asc", "desc"] = "asc"


@dataclass(frozen=True)
class OffsetPagination:
    """Skip *offset* records and return at most *limit*."""

    offset: int
    limit: int


@dataclass(frozen=True)
class PagePagination:
    """Return *page_size* records from 1-indexed *page*."""

    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


@dataclass(frozen=True)
class CursorPagination:
    """Keyset/cursor pagination.  *cursor* is ``None`` to start from the beginning."""

    limit: int
    cursor: str | None = None


Pagination = OffsetPagination | PagePagination | CursorPagination


@dataclass(frozen=True)
class Page(Generic[TItem]):
    """A page of results returned by ``get_many`` when *pagination* is provided.

    - *total*: total matching records, or ``None`` for ``CursorPagination``
      (running a full count defeats the purpose of cursor-based pagination).
    - *next_cursor*: opaque string for the next ``CursorPagination`` call;
      ``None`` when there are no more pages or when offset/page pagination is used.
    """

    items: list[TItem]
    total: int | None
    has_next: bool
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Cursor encoding / decoding
# ---------------------------------------------------------------------------

def encode_cursor(field_values: dict[str, Any], id_value: Any) -> str:
    """Encode ordering field values and aggregate id into an opaque cursor string.

    *field_values* keys are dot-joined field paths (e.g. ``"price"`` or
    ``"engine.price"``).  *id_value* is the raw ``aggregate.id.value``.
    """
    payload: dict[str, Any] = {"values": field_values, "id": id_value}
    return base64.urlsafe_b64encode(
        json.dumps(payload, default=_json_default).encode()
    ).decode()


def decode_cursor(cursor: str) -> tuple[dict[str, Any], Any]:
    """Decode a cursor string into ``(field_values, id_value)``.

    Inverse of :func:`encode_cursor`.
    """
    payload: dict[str, Any] = json.loads(
        base64.urlsafe_b64decode(cursor.encode()),
        object_hook=_json_hook,
    )
    return payload["values"], payload["id"]


def _json_default(obj: Any) -> Any:
    if isinstance(obj, _uuid_mod.UUID):
        return {"__uuid__": str(obj)}
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _json_hook(obj: dict[str, Any]) -> Any:
    if "__uuid__" in obj:
        return _uuid_mod.UUID(str(obj["__uuid__"]))
    return obj


# ---------------------------------------------------------------------------
# In-memory sort / keyset helpers  (shared by InMemory and Redis adapters)
# ---------------------------------------------------------------------------

def get_field_value(obj: Any, path: list[str]) -> Any:
    """Walk *path* on *obj*, unwrapping a ``ValueObject`` at the leaf."""
    val: Any = obj
    for name in path:
        val = getattr(val, name)
    if isinstance(val, ValueObject):
        return cast(Any, val).value
    return val


def _make_sort_key(path: list[str]) -> Callable[[Any], Any]:
    return lambda obj: get_field_value(obj, path)


def apply_ordering_in_memory(
    items: list[Any],
    ordering: list[OrderBy],
    *,
    id_tiebreaker: bool = False,
) -> list[Any]:
    """Sort *items* by *ordering*, respecting per-field direction.

    Uses successive stable sorts in reversed priority order — ``timsort``'s
    stability means the highest-priority field's sort wins while preserving
    relative order from lower-priority sorts for equal keys.

    When *id_tiebreaker* is ``True`` an implicit ``id ASC`` sort is prepended
    as the lowest priority key, ensuring cursor-page stability when ordering
    fields have duplicate values.
    """
    result = list(items)
    if id_tiebreaker:
        result.sort(key=_make_sort_key(["id"]))
    for ob in reversed(ordering):
        result.sort(key=_make_sort_key(ob.field.path), reverse=(ob.direction == "desc"))
    return result


def cursor_position(
    items: list[Any],
    cursor_values: dict[str, Any],
    cursor_id: Any,
    ordering: list[OrderBy],
) -> int:
    """Return the index of the first item AFTER the cursor pivot in *items*.

    *items* must already be sorted by *ordering* (with id tiebreaker).
    Returns 0 when the pivot is not found (e.g. the item was deleted
    between pages).
    """
    for i, item in enumerate(items):
        fields_match = all(
            get_field_value(item, ob.field.path) == cursor_values[".".join(ob.field.path)]
            for ob in ordering
        )
        item_id = get_field_value(item, ["id"])
        if fields_match and item_id == cursor_id:
            return i + 1
    return 0
