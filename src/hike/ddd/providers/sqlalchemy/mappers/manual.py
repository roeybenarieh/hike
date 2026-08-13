"""Manual (explicit ORM model) SQLAlchemy mappers.

Both mappers here require the caller to supply ready-made ORM model classes.
For zero-boilerplate alternatives that infer the schema automatically, see
:mod:`.auto`.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import InstrumentedAttribute

from hike.ddd.aggregate import Aggregate

from ..visitor import ISQLAlchemyMapper
from .basic import FlatMapperBase, inject_version


class DictSQLAlchemyMapper(ISQLAlchemyMapper):
    """Relational mapper: each entity class maps to its own ORM model/table.

    Pass a dict mapping every domain entity class (aggregate root and any
    nested entities) to its corresponding SQLAlchemy ORM model class.  The
    repository upserts nested entities depth-first before setting FK
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
                inject_version(model_cls)

    def get_model(self, entity_class: type) -> type:
        return self._mapping[entity_class]

    def get_column(self, model_class: type, field_name: str) -> InstrumentedAttribute[Any]:
        return getattr(model_class, field_name)


class FlatSQLAlchemyMapper(FlatMapperBase):
    """Embedded/single-table mapper: all entity fields become prefixed columns on the root ORM model.

    Nested ``Field[Entity]`` fields are flattened using ``sep`` (default ``"_"``)::

        engine.name  →  engine_name column
        engine.id    →  engine_id column

    ``list[Entity]`` fields are stored in a JSON/JSONB column under the field
    name.  The ORM model must declare all corresponding columns (scalar and JSON).

    Usage::

        from hike.ddd.providers.sqlalchemy import FlatSQLAlchemyMapper

        mapper = FlatSQLAlchemyMapper(MotorBoatModel)
        repo = SQLAlchemyRepository(MotorBoat, mapper)

    The ORM model must have columns: ``id``, ``name``, ``price``, ``engine_id``,
    ``engine_name``, ``engine_price`` (and a ``JSON``/``JSONB`` column for any
    ``list[Entity]`` fields).

    For a zero-boilerplate alternative that infers the schema automatically, see
    :class:`~.auto.FlatAutoSQLAlchemyMapper`.

    .. note::
        ``to_dict`` serialises ``EntityID`` fields as ``UUID`` objects. Databases
        or SQLAlchemy JSON types that do not accept ``UUID`` natively will need a
        custom ``json_serializer`` on the engine.
    """

    def __init__(self, root_model: type, sep: str = "_") -> None:
        super().__init__(root_model, sep)
