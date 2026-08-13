"""Auto-inferring SQLAlchemy mappers.

Both mappers here derive the full ORM schema from domain entity annotations,
requiring no hand-written ORM model classes.  For explicit-model alternatives,
see :mod:`.manual`.
"""
from __future__ import annotations

import re
from collections import deque
from typing import Any, cast, get_args, get_origin, get_type_hints

from sqlalchemy import Column, ForeignKey, Integer, JSON
from sqlalchemy.engine import Engine as SAEngine
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute, relationship

from hike.ddd.aggregate import Aggregate
from hike.ddd.entity import Entity, get_fields, unwrap_annotation, unwrap_field
from hike.ddd.value_object import ValueObject

from ..visitor import ISQLAlchemyMapper
from .basic import (
    FlatMapperBase,
    VERSION_ATTR,
    VERSION_COL,
    pk_sa_type,
    sa_col_type,
    strip_optional,
    table_name,
)


class DictAutoSQLAlchemyMapper(ISQLAlchemyMapper):
    """Relational mapper that infers the SQLAlchemy ORM schema automatically from
    domain entity annotations, eliminating the need to write explicit ORM model
    classes.

    All nested entity classes reachable from the aggregate root via ``Field[Entity]``
    or ``list[Entity]`` fields are discovered transitively.  One SQLAlchemy ORM
    model class is generated per entity class, using the relational strategy
    (one table per entity, same as :class:`~.manual.DictSQLAlchemyMapper`).

    Optionally pass a ``DeclarativeBase`` subclass as ``base`` to register the
    generated models in your existing metadata (for ``Base.metadata.create_all``).
    If omitted, an internal ``DeclarativeBase`` is created; use ``create_tables``
    to create the inferred tables.

    Usage::

        from hike.ddd.providers.sqlalchemy import DictAutoSQLAlchemyMapper, SQLAlchemyRepository
        from sqlalchemy.orm import DeclarativeBase

        class Base(DeclarativeBase): ...

        mapper = DictAutoSQLAlchemyMapper(MotorBoat, base=Base)
        Base.metadata.create_all(engine)        # or: mapper.create_tables(engine)
        repo = SQLAlchemyRepository(MotorBoat, mapper)

    Table names: PascalCase → snake_case plural (``MotorBoat`` → ``motor_boats``).

    ``Optional[Field[T]]`` / ``Field[T] | None`` → ``nullable=True``;
    all other non-PK/non-FK columns → ``nullable=False``.

    **Limitations**

    - No length/precision inference; uses unbounded ``String``, ``Float``, etc.
    - No index or unique constraint inference.
    - Column names always match field names; overrides require
      :class:`~.manual.DictSQLAlchemyMapper`.
    - Unmapped ``ValueObject`` raw types raise ``TypeError`` at construction time.
    - Table naming: snake_case plural only; not customisable without subclassing.
    """

    def __init__(
        self,
        aggregate_class: type[Entity[Any]],
        *,
        base: type[DeclarativeBase] | None = None,
    ) -> None:
        if base is None:
            base = cast(type[DeclarativeBase], type("_AutoDeclarativeBase", (DeclarativeBase,), {}))
        self._base = base
        self._mapping: dict[type, type] = {}
        self._build(aggregate_class)

    def _build(self, root: type[Entity[Any]]) -> None:
        # --- Pass 1: BFS discovery ---
        order: list[type[Any]] = []
        visited: set[type[Any]] = {root}
        single_refs: dict[type[Any], dict[str, type[Any]]] = {}
        list_refs: dict[type[Any], dict[str, type[Any]]] = {}

        q: deque[type[Any]] = deque([root])
        while q:
            cls = q.popleft()
            order.append(cls)
            try:
                hints = get_type_hints(cls)
            except Exception:  # noqa: BLE001
                hints = {}

            s: dict[str, type[Any]] = {}
            l: dict[str, type[Any]] = {}
            for f in get_fields(cls):
                if not f.init or f.name == "id":
                    continue
                ann = hints.get(f.name)
                if ann is None:
                    continue

                bare_ann, _ = strip_optional(ann)
                inner = unwrap_annotation(bare_ann)
                if (
                    inner is not None
                    and issubclass(inner, Entity)
                    and not issubclass(inner, ValueObject)
                ):
                    inner_cls = cast(type[Any], inner)
                    s[f.name] = inner_cls
                    if inner_cls not in visited:
                        visited.add(inner_cls)
                        q.append(inner_cls)
                    continue

                if get_origin(bare_ann) is list:
                    args = get_args(bare_ann)
                    elem = args[0] if args else None
                    if (
                        elem is not None
                        and issubclass(elem, Entity)
                        and not issubclass(elem, ValueObject)
                    ):
                        elem_cls = cast(type[Any], elem)
                        l[f.name] = elem_cls
                        if elem_cls not in visited:
                            visited.add(elem_cls)
                            q.append(elem_cls)

            single_refs[cls] = s
            list_refs[cls] = l

        # Back-FK info: child_fks[child_cls] = [(fk_col, parent_table, parent_cls)]
        child_fks: dict[type[Any], list[tuple[str, str, type[Any]]]] = {}
        for parent_cls, l_map in list_refs.items():
            parent_snake = re.sub(r"(?<!^)(?=[A-Z])", "_", parent_cls.__name__).lower()
            for child_cls in l_map.values():
                fk_col = f"{parent_snake}_id"
                child_fks.setdefault(child_cls, []).append((fk_col, parent_snake + "s", parent_cls))

        # --- Topological sort ---
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
            tbl = table_name(cls)
            try:
                hints = get_type_hints(cls)
            except Exception:  # noqa: BLE001
                hints = {}

            attrs: dict[str, Any] = {
                "__tablename__": tbl,
                "id": Column(pk_sa_type(cls), primary_key=True),
            }

            for f in get_fields(cls):
                if not f.init or f.name == "id":
                    continue
                ann = hints.get(f.name)
                if ann is None:
                    continue

                bare_ann, nullable = strip_optional(ann)

                if f.name in single_refs[cls]:
                    child_cls = single_refs[cls][f.name]
                    child_model = self._mapping[child_cls]
                    child_table = table_name(child_cls)
                    attrs[f"{f.name}_id"] = Column(pk_sa_type(child_cls), ForeignKey(f"{child_table}.id"), nullable=nullable)
                    attrs[f.name] = relationship(child_model)
                    continue

                if f.name in list_refs[cls]:
                    child_model = self._mapping[list_refs[cls][f.name]]
                    attrs[f.name] = relationship(child_model, cascade="all, delete-orphan", lazy="joined")
                    continue

                vo_cls = unwrap_field(bare_ann)
                if vo_cls is not None:
                    attrs[f.name] = Column(sa_col_type(vo_cls), nullable=nullable)

            for fk_col, parent_table, parent_cls in child_fks.get(cls, []):
                attrs[fk_col] = Column(pk_sa_type(parent_cls), ForeignKey(f"{parent_table}.id"), nullable=False)

            if issubclass(cls, Aggregate):
                attrs[VERSION_ATTR] = Column(VERSION_COL, Integer, nullable=False, default=0)

            self._mapping[cls] = type(f"{cls.__name__}AutoModel", (self._base,), attrs)  # type: ignore[misc]

    def create_tables(self, engine: SAEngine, *, checkfirst: bool = True) -> None:
        """Create all inferred relational tables in the database."""
        self._base.metadata.create_all(engine, checkfirst=checkfirst)

    def get_model(self, entity_class: type[Entity[Any]]) -> type:
        return self._mapping[entity_class]

    def get_column(self, model_class: type, field_name: str) -> InstrumentedAttribute[Any]:
        return getattr(model_class, field_name)


# ---------------------------------------------------------------------------
# FlatAutoSQLAlchemyMapper — schema-inference helpers (private to this module)
# ---------------------------------------------------------------------------

def _collect_flat_cols(
    entity_cls: type[Entity[Any]],
    attrs: dict[str, Any],
    prefix: str,
    sep: str,
    skip_id: bool,
    parent_nullable: bool = False,
) -> None:
    """Recursively add flattened columns for *entity_cls* to *attrs*."""
    try:
        hints: dict[str, Any] = get_type_hints(entity_cls)
    except Exception:  # noqa: BLE001
        hints = {}

    for f in get_fields(entity_cls):
        if not f.init:
            continue
        if skip_id and f.name == "id":
            continue

        col_name = f"{prefix}{sep}{f.name}" if prefix else f.name

        if f.name == "id":
            attrs[col_name] = Column(pk_sa_type(entity_cls), nullable=parent_nullable)
            continue

        ann = hints.get(f.name)
        if ann is None:
            continue

        bare_ann, nullable = strip_optional(ann)
        effective_nullable = nullable or parent_nullable

        if get_origin(bare_ann) is list:
            list_args = get_args(bare_ann)
            elem = list_args[0] if list_args else None
            if (
                elem is not None
                and isinstance(elem, type)
                and issubclass(elem, Entity)
                and not issubclass(elem, ValueObject)
            ):
                attrs[col_name] = Column(JSON, nullable=True)
                continue

        inner = unwrap_annotation(bare_ann)
        if (
            inner is not None
            and issubclass(inner, Entity)
            and not issubclass(inner, ValueObject)
        ):
            _collect_flat_cols(
                cast(type[Entity[Any]], inner),
                attrs,
                col_name,
                sep,
                skip_id=False,
                parent_nullable=effective_nullable,
            )
            continue

        vo_cls = unwrap_field(bare_ann)
        if vo_cls is not None:
            attrs[col_name] = Column(sa_col_type(vo_cls), nullable=effective_nullable)


def _build_flat_model(
    aggregate_class: type[Entity[Any]],
    base: type[DeclarativeBase],
    sep: str,
) -> type:
    tbl = table_name(aggregate_class)
    attrs: dict[str, Any] = {
        "__tablename__": tbl,
        "id": Column(pk_sa_type(aggregate_class), primary_key=True),
    }
    _collect_flat_cols(aggregate_class, attrs, prefix="", sep=sep, skip_id=True)
    return type(f"{aggregate_class.__name__}FlatAutoModel", (base,), attrs)  # type: ignore[misc]


class FlatAutoSQLAlchemyMapper(FlatMapperBase):
    """Single-table mapper that auto-infers the flat ORM schema from aggregate annotations.

    Unlike :class:`~.manual.FlatSQLAlchemyMapper` (which requires a hand-written
    ORM model), this mapper generates the ORM model automatically using the same
    flattening strategy: nested ``Field[Entity]`` fields become prefixed columns
    (``engine.name`` → ``engine_name``) and ``list[Entity]`` fields become a single
    JSON column.

    Optionally pass a ``DeclarativeBase`` subclass as ``base`` to register the
    generated model in your existing metadata (for ``Base.metadata.create_all``).
    When omitted, an internal base is used; call ``create_tables`` to create the table.

    Usage::

        from hike.ddd.providers.sqlalchemy import FlatAutoSQLAlchemyMapper, SQLAlchemyRepository

        mapper = FlatAutoSQLAlchemyMapper(MotorBoat)
        mapper.create_tables(engine)
        repo = SQLAlchemyRepository(MotorBoat, mapper)

    With an existing ``DeclarativeBase``::

        class Base(DeclarativeBase): ...

        mapper = FlatAutoSQLAlchemyMapper(MotorBoat, base=Base)
        Base.metadata.create_all(engine)
        repo = SQLAlchemyRepository(MotorBoat, mapper)

    **Schema inference**

    - ``Field[ValueObject]`` → scalar column
    - ``Optional[Field[ValueObject]]`` → ``nullable=True`` scalar column
    - ``Field[Entity]`` → all nested fields prefixed (``engine.name`` → ``engine_name``)
    - ``Optional[Field[Entity]]`` → same, but all nested columns are ``nullable=True``
    - ``list[Entity]`` → a single ``JSON`` column
    - Aggregate PK derived from the ``id`` annotation

    **Limitations** are the same as :class:`DictAutoSQLAlchemyMapper`: no
    length/precision inference, no index hints, column names always match field names.
    """

    def __init__(
        self,
        aggregate_class: type[Entity[Any]],
        *,
        base: type[DeclarativeBase] | None = None,
        sep: str = "_",
    ) -> None:
        if base is None:
            base = cast(type[DeclarativeBase], type("_FlatAutoDeclarativeBase", (DeclarativeBase,), {}))
        self._auto_base = base
        model = _build_flat_model(aggregate_class, base, sep)
        super().__init__(model, sep)

    def create_tables(self, engine: SAEngine, *, checkfirst: bool = True) -> None:
        """Create the inferred flat table in the database."""
        self._auto_base.metadata.create_all(engine, checkfirst=checkfirst)
