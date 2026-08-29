"""Ordering primitives for ``IRepository.get_many``."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, cast

from hike.specifications.proxy import TerminalFieldProxy
from hike.value_object import ValueObject


@dataclass(frozen=True)
class OrderBy:
    """Order results by *field* in the given *direction*."""

    field: TerminalFieldProxy
    direction: Literal["asc", "desc"] = "asc"


def asc(field: TerminalFieldProxy) -> OrderBy:
    """Return an ``OrderBy`` for *field* ascending."""
    return OrderBy(field=field, direction="asc")


def desc(field: TerminalFieldProxy) -> OrderBy:
    """Return an ``OrderBy`` for *field* descending."""
    return OrderBy(field=field, direction="desc")


# ---------------------------------------------------------------------------
# In-memory sort helper  (shared by InMemory and Redis adapters)
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
