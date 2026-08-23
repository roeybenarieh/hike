"""Shared foundation for all SQLAlchemy mapper implementations.

Provides:
- SA type-inference helpers used by both auto and manual mappers
- Optimistic-locking version column constants and ``inject_version``
- ``FlatMapperBase`` — the common base class for all single-table mappers
"""
from __future__ import annotations

import enum
import re
import types as _types
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Union, cast, get_args, get_origin, get_type_hints
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    Integer,
    Interval,
    JSON,
    LargeBinary,
    Numeric,
    String,
    Time,
    Uuid,
)
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import InstrumentedAttribute, column_property

from hike.entity import Entity, to_dict, unwrap_annotation
from hike.value_object import ValueObject

from ..visitor import ISQLAlchemyMapper

# ---------------------------------------------------------------------------
# SA type-inference helpers
# No leading underscore: these are legitimately shared across the mappers package.
# ---------------------------------------------------------------------------

_PY_TO_SA: dict[type, Any] = {
    str: String,
    int: Integer,
    float: Float,
    bool: Boolean,
    UUID: Uuid,
    bytes: LargeBinary,
    bytearray: LargeBinary,
    date: Date,
    datetime: DateTime,
    time: Time,
    timedelta: Interval,
    Decimal: Numeric,
    dict: JSON,
    list: JSON,
}


def strip_optional(ann: Any) -> tuple[Any, bool]:
    """Return ``(inner, True)`` for ``Optional[X]`` / ``X | None``, else ``(ann, False)``."""
    origin = get_origin(ann)
    if origin is Union or isinstance(ann, _types.UnionType):
        non_none = [a for a in get_args(ann) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0], True
    return ann, False


def table_name(cls: type) -> str:
    """PascalCase → snake_case plural (``MotorBoat`` → ``motor_boats``)."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).lower() + "s"


def _vo_raw_type(vo_cls: type) -> type:
    for klass in vo_cls.__mro__:
        for base in getattr(klass, "__orig_bases__", ()):
            if get_origin(base) is ValueObject:
                args = get_args(base)
                if args:
                    return args[0]  # type: ignore[return-value]
    return str


def pk_sa_type(entity_cls: type[Any]) -> Any:
    """Infer the SA column type for an entity PK from ``id: Field[EntityID[T]]``."""
    try:
        hints = get_type_hints(entity_cls)
    except Exception:  # noqa: BLE001
        return Uuid
    id_ann = hints.get("id")
    if id_ann is None:
        return Uuid
    field_args = get_args(id_ann)        # Field[EntityID[UUID]] → (EntityID[UUID],)
    if not field_args:
        return Uuid
    id_args = get_args(field_args[0])    # EntityID[UUID] → (UUID,)
    if id_args:
        sa = _PY_TO_SA.get(id_args[0])
        if sa is not None:
            return sa
    return Uuid


def sa_col_type(vo_cls: type) -> Any:
    """Infer the SA column type for a ``ValueObject[T]`` subclass."""
    raw = _vo_raw_type(vo_cls)
    sa = _PY_TO_SA.get(raw)
    if sa is not None:
        return sa
    if issubclass(raw, enum.Enum):
        return SAEnum(raw)
    raise TypeError(
        f"Cannot infer SQLAlchemy column type for {vo_cls.__name__!r}: "
        f"raw type {raw.__name__!r} has no SA mapping. "
        f"Use DictSQLAlchemyMapper with an explicit model instead."
    )


# ---------------------------------------------------------------------------
# Optimistic-locking version column
# ---------------------------------------------------------------------------

# SQL column name — double-underscore prefix avoids collisions with user columns.
VERSION_COL = '__hike_version'
# Python ORM attribute name — single-underscore because SQLAlchemy's declarative
# metaclass silently skips dunder attribute names.
VERSION_ATTR = '_hike_version'


def inject_version(model_cls: type) -> None:
    """Inject the ``__hike_version`` optimistic-locking column into *model_cls*.

    Called by :class:`FlatMapperBase` (for all flat-table mappers) and by
    :class:`~.manual.DictSQLAlchemyMapper` for aggregate root models.

    Raises ``ValueError`` if *model_cls* already declares ``__hike_version`` —
    that column is reserved for Hike's internal optimistic locking.

    **Ordering requirement:** instantiate the mapper *before*
    ``metadata.create_all()`` so the injected column is included in the
    ``CREATE TABLE`` statement.
    """
    table = getattr(model_cls, '__table__', None)
    if table is None:
        return
    if VERSION_COL in table.c:
        raise ValueError(
            f"ORM model {model_cls.__name__!r} must not declare {VERSION_COL!r}: "
            f"this column is reserved for hike's internal optimistic locking."
        )
    col = Column(VERSION_COL, Integer, nullable=False, default=0)
    table.append_column(col)
    sa_inspect(model_cls).add_property(  # type: ignore[union-attr]
        VERSION_ATTR, column_property(table.c[VERSION_COL])
    )


# ---------------------------------------------------------------------------
# FlatMapperBase — shared expand/collect logic for all single-table mappers
# ---------------------------------------------------------------------------

class FlatMapperBase(ISQLAlchemyMapper):
    """Common base for all single-table (flat) mapper implementations.

    Subclasses supply a ready ORM model and a separator string, either
    hand-written (:class:`~.manual.FlatSQLAlchemyMapper`) or auto-generated
    (:class:`~.auto.FlatAutoSQLAlchemyMapper`).  This class injects the
    version column and implements all expand/collect serialisation.
    """

    def __init__(self, root_model: type, sep: str) -> None:
        inject_version(root_model)
        self._root_model = root_model
        self._sep = sep

    # -- ISQLAlchemyMapper --

    def get_model(self, entity_class: type[Entity[Any]]) -> type:
        return self._root_model

    def get_column(self, model_class: type, field_name: str) -> InstrumentedAttribute[Any]:
        return getattr(model_class, field_name)

    def resolve_path(self, path: list[str]) -> InstrumentedAttribute[Any] | None:
        col_name = self._sep.join(path)
        return cast(InstrumentedAttribute[Any] | None, getattr(self._root_model, col_name, None))

    def expand_nested(self, entity: Any, entity_cls: type, field_name: str) -> dict[str, Any]:
        return self._flatten(entity, field_name)

    def collect_nested(self, orm_obj: Any, entity_cls: type, field_name: str) -> dict[str, Any] | None:
        return self._collect(orm_obj, entity_cls, field_name)

    def expand_list_nested(
        self, entities: list[Any], entity_cls: type, field_name: str
    ) -> dict[str, Any] | None:
        return {field_name: [to_dict(e) for e in entities]}

    def collect_list_nested(
        self, orm_obj: Any, entity_cls: type, field_name: str
    ) -> list[dict[str, Any]] | None:
        return cast(list[dict[str, Any]] | None, getattr(orm_obj, field_name, None))

    # -- serialisation helpers --

    def _flatten(self, entity: Any, prefix: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name in entity.get_init_field_names():
            val: Any = getattr(entity, name)
            col = f"{prefix}{self._sep}{name}"
            if isinstance(val, ValueObject):
                result[col] = cast(Any, val).value
            elif isinstance(val, Entity):
                result.update(self._flatten(val, col))
            elif isinstance(val, list):
                result[col] = [
                    to_dict(cast(Entity[Any], v)) if isinstance(v, Entity)
                    else cast(Any, v).value if isinstance(v, ValueObject)
                    else v
                    for v in cast(list[Any], val)
                ]
        return result

    def _collect(self, orm_obj: Any, entity_cls: type, prefix: str) -> dict[str, Any]:
        try:
            hints: dict[str, Any] = get_type_hints(entity_cls)
        except Exception:  # noqa: BLE001
            hints = {}
        data: dict[str, Any] = {}
        for name in cast(type[Entity[Any]], entity_cls).get_init_field_names():
            col = f"{prefix}{self._sep}{name}"
            ann = hints.get(name)
            inner = unwrap_annotation(ann) if ann else None
            if isinstance(inner, type) and issubclass(inner, Entity):
                data[name] = self._collect(orm_obj, cast(type, inner), col)
            else:
                data[name] = getattr(orm_obj, col, None)
        return data
