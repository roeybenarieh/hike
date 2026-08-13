from __future__ import annotations

from typing import Any, cast, get_origin, get_type_hints

from sqlalchemy.orm import Session

from hike.ddd.entity import Entity, EntityID, Field, from_dict, get_fields, to_dict
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

from .mappers import DictAutoSQLAlchemyMapper, VERSION_ATTR
from .visitor import ISQLAlchemyMapper, SQLAlchemyEvaluationSpecificationVisitor


class SQLAlchemyRepository(IRepository[TId, Session, TAggregate]):
    """Generic SQLAlchemy ORM repository with optimistic concurrency control.

    ``aggregate_class`` is the domain aggregate root class.  ``mapper`` bridges
    domain entities and SQLAlchemy ORM models.  When omitted, a
    ``DictAutoSQLAlchemyMapper`` is created automatically — Hike infers the full
    ORM schema from the aggregate's field annotations.

    Four concrete mapper implementations are available:
    - ``DictAutoSQLAlchemyMapper`` (default): relational schema inferred from annotations.
    - ``FlatAutoSQLAlchemyMapper``: flat single-table schema inferred from annotations.
    - ``DictSQLAlchemyMapper``: relational, one ORM model per entity class.
    - ``FlatSQLAlchemyMapper``: embedded, all fields on a single root model.

    Supported aggregate shapes:
    - Flat: all ``Field[ValueObject]`` fields map to columns on the root ORM model.
    - ``list[Entity]``: child entities stored in a related table (cascade) or a JSON
      column (embedded mapper).
    - ``Field[Entity]``: single nested entity, handled recursively at any depth.

    Install with: ``pip install hike[sqlalchemy]``
    """

    def __init__(
        self,
        aggregate_class: type[TAggregate],
        mapper: ISQLAlchemyMapper | None = None,
    ) -> None:
        super().__init__()
        self._aggregate_class = aggregate_class
        if mapper is None:
            mapper = DictAutoSQLAlchemyMapper(aggregate_class)
        self._mapper = mapper
        self._model_class = mapper.get_model(aggregate_class)

    def _hints(self) -> dict[str, Any]:
        try:
            return get_type_hints(self._aggregate_class)
        except Exception:
            return {}

    def _list_entity_fields(self, hints: dict[str, Any]) -> dict[str, type]:
        """Return {field_name: elem_cls} for list[Entity] fields."""
        result: dict[str, type] = {}
        for name, ann in hints.items():
            if get_origin(ann) is not list:
                continue
            args: tuple[Any, ...] = getattr(ann, "__args__", ())
            elem_cls = args[0] if args else None
            if isinstance(elem_cls, type) and issubclass(elem_cls, Entity):
                result[name] = elem_cls
        return result

    def _single_entity_fields(self, hints: dict[str, Any]) -> dict[str, type]:
        """Return {field_name: entity_cls} for Field[Entity] (non-list) fields."""
        result: dict[str, type] = {}
        for name, ann in hints.items():
            if get_origin(ann) is not Field:
                continue
            args: tuple[Any, ...] = getattr(ann, "__args__", ())
            inner = args[0] if args else None
            bare = inner if isinstance(inner, type) else get_origin(inner)
            if isinstance(bare, type) and issubclass(bare, Entity):
                result[name] = bare
        return result

    def _sync_entity(self, entity: Any, entity_cls: type) -> Any:
        """Recursively upsert a nested entity's ORM row (depth-first) and return the tracked model."""
        try:
            hints: dict[str, Any] = get_type_hints(entity_cls)
        except Exception:
            hints = {}
        sub_single = self._single_entity_fields(hints)
        model_cls = self._mapper.get_model(entity_cls)
        entity_dict = to_dict(entity)

        for sub_name, sub_cls in sub_single.items():
            sub_entity: Any = getattr(entity, sub_name, None)
            if sub_entity is None:
                continue
            expanded = self._mapper.expand_nested(sub_entity, sub_cls, sub_name)
            if expanded:
                entity_dict.pop(sub_name, None)
                entity_dict.update(expanded)
            else:
                entity_dict[sub_name] = self._sync_entity(sub_entity, sub_cls)

        return self.session.merge(model_cls(**entity_dict))

    def _reconstruct_entity_dict(self, orm_obj: Any, entity_cls: type) -> dict[str, Any]:
        """Recursively read raw field values from *orm_obj* for reconstructing *entity_cls*."""
        try:
            hints: dict[str, Any] = get_type_hints(entity_cls)
        except Exception:
            hints = {}
        sub_single = self._single_entity_fields(hints)
        init_names = {f.name for f in get_fields(cast(type[Entity[Any]], entity_cls)) if f.init}
        result: dict[str, Any] = {}
        for k in init_names:
            if not hasattr(orm_obj, k):
                continue
            v: Any = getattr(orm_obj, k)
            if k in sub_single and v is not None:
                sub_dict = self._mapper.collect_nested(orm_obj, sub_single[k], k)
                result[k] = from_dict(
                    sub_single[k],
                    sub_dict if sub_dict is not None else self._reconstruct_entity_dict(v, sub_single[k]),
                )
            else:
                result[k] = v
        return result

    def _to_model_dict(self, aggregate: TAggregate) -> dict[str, Any]:
        """Serialize *aggregate* to a dict suitable for constructing an ORM model.

        - ``list[Entity]`` fields: mapper override (JSON) or relational ORM models.
        - ``Field[Entity]`` fields: mapper override (embedded) or ``session.merge()`` (relational).
        """
        hints = self._hints()
        list_fields = self._list_entity_fields(hints)
        single_fields = self._single_entity_fields(hints)
        flat = to_dict(aggregate)

        for name, elem_cls in list_fields.items():
            if name not in flat:
                continue
            entities: list[Any] = cast(list[Any], getattr(aggregate, name))
            expanded = self._mapper.expand_list_nested(entities, elem_cls, name)
            if expanded is not None:
                flat.update(expanded)
            else:
                nested_model_cls = self._mapper.get_model(elem_cls)
                flat[name] = [nested_model_cls(**item) for item in cast(list[Any], flat[name])]

        for name, entity_cls in single_fields.items():
            nested_entity: Any = getattr(aggregate, name, None)
            if nested_entity is None:
                continue
            expanded = self._mapper.expand_nested(nested_entity, entity_cls, name)
            if expanded:
                flat.pop(name, None)
                flat.update(expanded)
            else:
                flat[name] = self._sync_entity(nested_entity, entity_cls)

        return flat

    def _from_model(self, model: Any) -> TAggregate:
        hints = self._hints()
        list_fields = self._list_entity_fields(hints)
        single_fields = self._single_entity_fields(hints)
        init_names = {f.name for f in get_fields(self._aggregate_class) if f.init}
        data: dict[str, Any] = {}
        for name in init_names:
            if name in list_fields:
                elem_cls = list_fields[name]
                json_list = self._mapper.collect_list_nested(model, elem_cls, name)
                if json_list is not None:
                    data[name] = [from_dict(elem_cls, item) for item in json_list]
                elif hasattr(model, name):
                    val: Any = getattr(model, name)
                    elem_init_names = {f.name for f in get_fields(cast(type[Entity[Any]], elem_cls)) if f.init}
                    data[name] = [
                        from_dict(elem_cls, {k: getattr(item, k) for k in elem_init_names if hasattr(item, k)})
                        for item in cast(list[Any], val)
                    ]
            elif name in single_fields:
                entity_cls = single_fields[name]
                raw = self._mapper.collect_nested(model, entity_cls, name)
                if raw is not None:
                    data[name] = from_dict(entity_cls, raw)
                elif hasattr(model, name):
                    val = getattr(model, name)
                    if val is not None:
                        data[name] = from_dict(entity_cls, self._reconstruct_entity_dict(val, entity_cls))
            elif hasattr(model, name):
                data[name] = getattr(model, name)
        aggregate = self._aggregate_class(**data)
        set_version(aggregate, getattr(model, VERSION_ATTR))
        return aggregate

    def save(self, aggregate: TAggregate) -> TId:
        model = self._model_class(**self._to_model_dict(aggregate))
        try:
            self.session.add(model)
            self.session.flush()
        except Exception as exc:
            raise AggregateAlreadyExistError(aggregate) from exc
        set_version(aggregate, getattr(model, VERSION_ATTR))
        return aggregate.id  # pyright: ignore[reportReturnType]

    def _delete(self, identifier: EntityID[TId]) -> None:
        model = self.session.get(self._model_class, identifier.value)
        if model is None:
            raise AggregateDoesNotExistError(identifier)
        self.session.delete(model)

    def get_one(self, identifier: EntityID[TId]) -> TAggregate:
        model = self.session.get(self._model_class, identifier.value)
        if model is None:
            raise AggregateDoesNotExistError(identifier)
        return self._from_model(model)

    def get_many(self, specification: ISpecification) -> list[TAggregate]:
        visitor = SQLAlchemyEvaluationSpecificationVisitor(self._aggregate_class, self._mapper)
        specification.accept(visitor)
        stmt = visitor.result()
        rows = self.session.scalars(stmt).all()
        return [self._from_model(row) for row in rows]

    def update(self, aggregate: TAggregate) -> None:
        model = self.session.get(self._model_class, aggregate.id.value)
        if model is None:
            raise AggregateDoesNotExistError(aggregate)
        if getattr(model, VERSION_ATTR) != get_version(aggregate):
            raise OptimisticLockError(aggregate)
        for key, val in self._to_model_dict(aggregate).items():
            setattr(model, key, val)
        setattr(model, VERSION_ATTR, getattr(model, VERSION_ATTR) + 1)
        set_version(aggregate, get_version(aggregate) + 1)

    def upsert(self, aggregate: TAggregate) -> None:
        model = self.session.get(self._model_class, aggregate.id.value)
        if model is None:
            self.session.add(self._model_class(**self._to_model_dict(aggregate)))
        else:
            for key, val in self._to_model_dict(aggregate).items():
                setattr(model, key, val)
            setattr(model, VERSION_ATTR, getattr(model, VERSION_ATTR) + 1)
