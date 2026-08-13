from __future__ import annotations

from typing import Any, cast, get_type_hints

from sqlalchemy import Column, Integer
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import InstrumentedAttribute, column_property

from hike.ddd.aggregate import Aggregate
from hike.ddd.entity import Entity, get_fields, to_dict, unwrap_annotation
from hike.ddd.value_object import ValueObject

from .visitor import ISQLAlchemyMapper

# SQL column name — double-underscore prefix makes accidental collisions with
# user-declared columns extremely unlikely.
VERSION_COL = '__hike_version'
# Python ORM attribute name on the model — single-underscore because SQLAlchemy's
# declarative metaclass skips dunder (``__name``) attribute names.
VERSION_ATTR = '_hike_version'


def _inject_version(model_cls: type) -> None:
    """Inject a ``__hike_version`` integer column into *model_cls*.

    Called automatically by :class:`DictSQLAlchemyMapper` (for aggregate root
    models) and :class:`FlatSQLAlchemyMapper` (for the root model).

    Raises ``ValueError`` if *model_cls* already declares a ``__hike_version``
    column — it is reserved for hike's internal optimistic locking.

    **Ordering requirement:** the mapper must be instantiated *before*
    ``metadata.create_all()`` so the injected column is included in the
    ``CREATE TABLE`` statement.  Declare mappers at module level, the same way
    ``AutoSQLAlchemyMapper`` instances are declared.
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
    sa_inspect(model_cls).add_property(VERSION_ATTR, column_property(table.c[VERSION_COL]))


class DictSQLAlchemyMapper(ISQLAlchemyMapper):
    """Relational mapper: each entity class maps to its own ORM model/table.

    Pass a dict mapping every domain entity class (aggregate root and any nested
    entities) to its corresponding SQLAlchemy ORM model class.  The repository
    will upsert nested entities in a depth-first order before setting FK
    relationships on the parent model.

    Usage::

        from hike.ddd.providers.sqlalchemy import DictSQLAlchemyMapper

        mapper = DictSQLAlchemyMapper({
            MotorBoat: MotorBoatModel,
            BoatEngine: EngineModel,
        })
        repo = SQLAlchemyRepository(MotorBoat, mapper)

    The ORM models must declare SQLAlchemy ``relationship()`` attributes for any
    ``Field[Entity]`` field so that setting ``model.engine = tracked_engine_model``
    automatically resolves the FK column.
    """

    def __init__(self, mapping: dict[type, type]) -> None:
        self._mapping = mapping
        for entity_cls, model_cls in mapping.items():
            if issubclass(entity_cls, Aggregate):
                _inject_version(model_cls)

    def get_model(self, entity_class: type) -> type:
        return self._mapping[entity_class]

    def get_column(self, model_class: type, field_name: str) -> InstrumentedAttribute[Any]:
        return getattr(model_class, field_name)


class FlatSQLAlchemyMapper(ISQLAlchemyMapper):
    """Embedded/single-table mapper: all entity fields become prefixed columns on the root ORM model.

    Nested ``Field[Entity]`` fields are flattened using ``sep`` (default ``"_"``)::

        engine.name  →  engine_name column
        engine.id    →  engine_id column

    ``list[Entity]`` fields are stored in a JSON/JSONB column under the field name.
    The ORM model must declare all corresponding columns (scalar and JSON).

    Usage::

        from hike.ddd.providers.sqlalchemy import FlatSQLAlchemyMapper

        mapper = FlatSQLAlchemyMapper(MotorBoatModel)
        repo = SQLAlchemyRepository(MotorBoat, mapper)

    The ORM model must have columns: ``id``, ``name``, ``price``, ``engine_id``,
    ``engine_name``, ``engine_price`` (and a ``JSON``/``JSONB`` column for any
    ``list[Entity]`` fields).

    .. note::
        ``to_dict`` serialises ``EntityID`` fields as ``UUID`` objects. Databases
        or SQLAlchemy JSON types that do not accept ``UUID`` natively will need a
        custom ``json_serializer`` configured on the engine.
    """

    def __init__(self, root_model: type, sep: str = "_") -> None:
        self._root_model = root_model
        self._sep = sep
        _inject_version(root_model)

    def get_model(self, entity_class: type) -> type:
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

    # -- internal helpers --

    def _flatten(self, entity: Any, prefix: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for f in get_fields(entity):
            if not f.init:
                continue
            val: Any = getattr(entity, f.name)
            col = f"{prefix}{self._sep}{f.name}"
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
        except Exception:
            hints = {}
        data: dict[str, Any] = {}
        for f in get_fields(cast(type[Entity[Any]], entity_cls)):
            if not f.init:
                continue
            col = f"{prefix}{self._sep}{f.name}"
            ann = hints.get(f.name)
            inner = unwrap_annotation(ann) if ann else None
            if isinstance(inner, type) and issubclass(inner, Entity):
                data[f.name] = self._collect(orm_obj, cast(type, inner), col)
            else:
                data[f.name] = getattr(orm_obj, col, None)
        return data
