from __future__ import annotations

import enum
import re
import types as _types
from collections import deque
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
    ForeignKey,
    Integer,
    Interval,
    JSON,
    LargeBinary,
    Numeric,
    String,
    Time,
    Uuid,
)
from sqlalchemy.engine import Engine as SAEngine
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute, relationship

from hike.ddd.aggregate import Aggregate
from hike.ddd.entity import Entity, get_fields, unwrap_annotation, unwrap_field
from hike.ddd.value_object import ValueObject

from .mappers import VERSION_ATTR, VERSION_COL
from .visitor import ISQLAlchemyMapper


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


def _strip_optional(ann: Any) -> tuple[Any, bool]:
    """Strip ``None`` from a union annotation and report whether it was optional.

    Returns ``(inner, True)`` for ``Optional[X]`` / ``X | None``, else ``(ann, False)``.
    Handles both ``typing.Union`` (``Optional[X]``) and ``types.UnionType`` (``X | None``).
    """
    origin = get_origin(ann)
    if origin is Union or isinstance(ann, _types.UnionType):
        non_none = [a for a in get_args(ann) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0], True
    return ann, False


def _table_name(cls: type) -> str:
    """PascalCase class name → snake_case plural (e.g. ``MotorBoat`` → ``motor_boats``)."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).lower() + "s"


def _vo_raw_type(vo_cls: type) -> type:
    """Extract raw Python type T from a ``ValueObject[T]`` subclass via ``__orig_bases__``."""
    for klass in vo_cls.__mro__:
        for base in getattr(klass, "__orig_bases__", ()):
            if get_origin(base) is ValueObject:
                args = get_args(base)
                if args:
                    return args[0]
    return str


def _pk_sa_type(entity_cls: type[Any]) -> Any:
    """Infer SA column type for an entity's PK from its ``id: Field[EntityID[T]]`` annotation.

    Traverses ``Field[EntityID[T]]`` → ``EntityID[T]`` → ``T`` → ``_PY_TO_SA``.
    Falls back to ``Uuid`` for bare or unrecognised ID types.
    """
    try:
        hints = get_type_hints(entity_cls)
    except Exception:
        return Uuid
    id_ann = hints.get("id")
    if id_ann is None:
        return Uuid
    field_args = get_args(id_ann)           # Field[EntityID[UUID]] → (EntityID[UUID],)
    if not field_args:
        return Uuid
    id_args = get_args(field_args[0])       # EntityID[UUID] → (UUID,)
    if id_args:
        sa = _PY_TO_SA.get(id_args[0])
        if sa is not None:
            return sa
    return Uuid


def _sa_col_type(vo_cls: type) -> Any:
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


class AutoSQLAlchemyMapper(ISQLAlchemyMapper):
    """Relational mapper that infers the SQLAlchemy ORM schema automatically from
    domain entity annotations, eliminating the need to write explicit ORM model
    classes.

    All nested entity classes reachable from the aggregate root via ``Field[Entity]``
    or ``list[Entity]`` fields are discovered transitively.  One SQLAlchemy ORM
    model class is generated per entity class, using the relational strategy
    (one table per entity, same as ``DictSQLAlchemyMapper``).

    Optionally pass a ``DeclarativeBase`` subclass as ``base`` to register the
    generated models in your existing metadata (useful for ``Base.metadata.create_all``).
    If omitted, an internal ``DeclarativeBase`` is created; use ``create_tables`` to
    create the inferred tables.

    Usage::

        from hike.ddd.providers.sqlalchemy import AutoSQLAlchemyMapper, SQLAlchemyRepository
        from sqlalchemy.orm import DeclarativeBase

        class Base(DeclarativeBase): ...

        # MotorBoat's nested BoatEngine entity is discovered automatically.
        mapper = AutoSQLAlchemyMapper(MotorBoat, base=Base)
        Base.metadata.create_all(engine)        # or: mapper.create_tables(engine)
        repo = SQLAlchemyRepository(MotorBoat, mapper)

    Table names are derived by converting the class name from PascalCase to
    snake_case and appending ``s`` (e.g. ``MotorBoat`` → ``motor_boats``).

    ``Optional[Field[T]]`` / ``Field[T] | None`` annotations produce
    ``nullable=True`` columns; all other non-PK, non-FK columns are
    ``nullable=False``.

    **Limitations**

    - *Column constraints*: length/precision cannot be inferred; uses unbounded
      ``String``, ``Float``, etc.  Add explicit constraints via ``DictSQLAlchemyMapper``.
    - *No index or unique constraint inference.*
    - *Custom column names*: the field name is always used as-is; overrides
      require ``DictSQLAlchemyMapper``.
    - *Unmapped* ``ValueObject`` *raw types* raise ``TypeError`` at construction
      time if the inner Python type has no SQLAlchemy equivalent.
    - *Table naming*: snake_case plural only; not customisable without subclassing.
    """

    def __init__(
        self,
        aggregate_class: type[Entity[Any]],
        *,
        base: type[DeclarativeBase] | None = None,
    ) -> None:
        if base is None:
            base = type("_AutoDeclarativeBase", (DeclarativeBase,), {})  # type: ignore[misc]
        self._base = base
        self._mapping: dict[type, type] = {}
        self._build(aggregate_class)

    def _build(self, root: type[Entity[Any]]) -> None:
        # --- Pass 1: BFS discovery ---
        order: list[type[Any]] = []
        visited: set[type[Any]] = {root}
        # {cls: {field_name: entity_cls}} for Field[Entity] fields
        single_refs: dict[type[Any], dict[str, type[Any]]] = {}
        # {cls: {field_name: elem_cls}} for list[Entity] fields
        list_refs: dict[type[Any], dict[str, type[Any]]] = {}

        q: deque[type[Any]] = deque([root])
        while q:
            cls = q.popleft()
            order.append(cls)
            try:
                hints = get_type_hints(cls)
            except Exception:
                hints = {}

            s: dict[str, type[Any]] = {}
            l: dict[str, type[Any]] = {}
            for f in get_fields(cls):
                if not f.init or f.name == "id":
                    continue
                ann = hints.get(f.name)
                if ann is None:
                    continue

                # Field[Entity] or Optional[Field[Entity]] (non-ValueObject inner type)
                bare_ann, _ = _strip_optional(ann)
                inner = unwrap_annotation(bare_ann)
                if (
                    inner is not None
                    and issubclass(inner, Entity)
                    and not issubclass(inner, ValueObject)
                ):
                    # cast: issubclass narrows to type[Entity[Unknown]]; widen to type[Any]
                    inner_cls = cast(type[Any], inner)
                    s[f.name] = inner_cls
                    if inner_cls not in visited:
                        visited.add(inner_cls)
                        q.append(inner_cls)
                    continue

                # list[Entity]
                if get_origin(bare_ann) is list:
                    args = get_args(bare_ann)
                    elem = args[0] if args else None
                    if (
                        elem is not None
                        and issubclass(elem, Entity)
                        and not issubclass(elem, ValueObject)
                    ):
                        # cast: issubclass narrows Any to type[Entity[Unknown]]; widen to type[Any]
                        elem_cls = cast(type[Any], elem)
                        l[f.name] = elem_cls
                        if elem_cls not in visited:
                            visited.add(elem_cls)
                            q.append(elem_cls)

            single_refs[cls] = s
            list_refs[cls] = l

        # Back-FK info: child_fks[child_cls] = [(fk_col_name, parent_table, parent_cls)]
        child_fks: dict[type[Any], list[tuple[str, str, type[Any]]]] = {}
        for parent_cls, l_map in list_refs.items():
            parent_snake = re.sub(r"(?<!^)(?=[A-Z])", "_", parent_cls.__name__).lower()
            for child_cls in l_map.values():
                fk_col = f"{parent_snake}_id"
                child_fks.setdefault(child_cls, []).append((fk_col, parent_snake + "s", parent_cls))

        # --- Topological sort ---
        # Both Field[Entity] and list[Entity] children must be created before their parent
        # (the parent's model references the child model class in relationship()).
        in_deg: dict[type[Any], int] = {c: 0 for c in order}
        succ: dict[type[Any], list[type[Any]]] = {c: [] for c in order}
        for cls in order:
            for dep in (*single_refs[cls].values(), *list_refs[cls].values()):
                in_deg[cls] += 1
                succ[dep].append(cls)

        topo: list[type[Any]] = []
        ready: deque[type[Any]] = deque(c for c in order if in_deg[c] == 0)
        while ready:
            cls = ready.popleft()
            topo.append(cls)
            for nxt in succ[cls]:
                in_deg[nxt] -= 1
                if in_deg[nxt] == 0:
                    ready.append(nxt)
        if len(topo) != len(order):
            topo = order  # cycle fallback: BFS order

        # --- Pass 2: build ORM model classes in topo order ---
        for cls in topo:
            table = _table_name(cls)
            try:
                hints = get_type_hints(cls)
            except Exception:
                hints = {}

            pk_type = _pk_sa_type(cls)
            attrs: dict[str, Any] = {
                "__tablename__": table,
                "id": Column(pk_type, primary_key=True),
            }

            for f in get_fields(cls):
                if not f.init or f.name == "id":
                    continue
                ann = hints.get(f.name)
                if ann is None:
                    continue

                bare_ann, nullable = _strip_optional(ann)

                # Field[Entity] or Optional[Field[Entity]] → FK column + relationship
                if f.name in single_refs[cls]:
                    child_cls = single_refs[cls][f.name]
                    child_model = self._mapping[child_cls]
                    child_table = _table_name(child_cls)
                    attrs[f"{f.name}_id"] = Column(_pk_sa_type(child_cls), ForeignKey(f"{child_table}.id"), nullable=nullable)
                    attrs[f.name] = relationship(child_model)
                    continue

                # list[Entity] → cascade relationship
                if f.name in list_refs[cls]:
                    child_model = self._mapping[list_refs[cls][f.name]]
                    attrs[f.name] = relationship(child_model, cascade="all, delete-orphan", lazy="joined")
                    continue

                # Field[ValueObject] or Optional[Field[ValueObject]] → scalar column
                vo_cls = unwrap_field(bare_ann)
                if vo_cls is not None:
                    attrs[f.name] = Column(_sa_col_type(vo_cls), nullable=nullable)

            # Back-FK columns for list[Entity] children
            for fk_col, parent_table, parent_cls in child_fks.get(cls, []):
                attrs[fk_col] = Column(_pk_sa_type(parent_cls), ForeignKey(f"{parent_table}.id"), nullable=False)

            # version column — Aggregate roots only
            if issubclass(cls, Aggregate):
                attrs[VERSION_ATTR] = Column(VERSION_COL, Integer, nullable=False, default=0)

            self._mapping[cls] = type(f"{cls.__name__}AutoModel", (self._base,), attrs)  # type: ignore[misc]

    def create_tables(self, engine: SAEngine, *, checkfirst: bool = True) -> None:
        """Create all inferred tables in the database."""
        self._base.metadata.create_all(engine, checkfirst=checkfirst)

    def get_model(self, entity_class: type[Entity[Any]]) -> type:
        return self._mapping[entity_class]

    def get_column(self, model_class: type, field_name: str) -> InstrumentedAttribute[Any]:
        return getattr(model_class, field_name)
