from __future__ import annotations

from typing import Any, cast, get_origin, get_type_hints

from sqlalchemy.orm import Session

from hike.ddd.entity import Entity, EntityID, from_dict, get_fields, to_dict
from hike.ddd.repository import (
    AggregateAlreadyExistError,
    AggregateDoesNotExistError,
    IRepository,
    OptimisticLockError,
    TAggregate,
    TId,
)
from hike.ddd.specifications import ISpecification

from .visitor import ISQLAlchemyMapper, SQLAlchemyEvaluationSpecificationVisitor


class SQLAlchemyRepository(IRepository[TId, Session, TAggregate]):
    """Generic SQLAlchemy ORM repository with optimistic concurrency control.

    ``aggregate_class`` is the domain aggregate root class.  ``mapper`` provides
    the bridge between domain entity classes and SQLAlchemy ORM models/columns —
    implement :class:`~hike.ddd.providers.sqlalchemy.visitor.ISQLAlchemyMapper`
    to tell the repository which model class corresponds to the aggregate and
    how each ValueObject field maps to a column.

    For aggregates with ``list[SomeEntity]`` fields, the mapper must also handle
    ``get_model(SomeEntity)`` so the repository can convert between ORM model
    objects and domain entities automatically.

    ``get_many`` translates the specification tree into a SQL WHERE clause via
    :class:`~hike.ddd.providers.sqlalchemy.visitor.SQLAlchemyEvaluationSpecificationVisitor`.

    **Optimistic locking** — the ORM model must expose a ``version`` integer
    column (``default=0``).  ``update`` compares the model's stored version
    against ``aggregate.version`` and raises ``OptimisticLockError`` on mismatch.
    ``aggregate.version`` is incremented on every successful write.

    Install with: ``pip install hike[sqlalchemy]``
    """

    def __init__(
        self,
        aggregate_class: type[TAggregate],
        mapper: ISQLAlchemyMapper,
    ) -> None:
        super().__init__()
        self._aggregate_class = aggregate_class
        self._mapper = mapper
        self._model_class = mapper.get_model(aggregate_class)

    def _hints(self) -> dict[str, Any]:
        try:
            return get_type_hints(self._aggregate_class)
        except Exception:
            return {}

    def _list_entity_fields(self, hints: dict[str, Any]) -> dict[str, type]:
        """Return {field_name: elem_cls} for list[Entity] fields in this aggregate."""
        result: dict[str, type] = {}
        for name, ann in hints.items():
            if get_origin(ann) is not list:
                continue
            args: tuple[Any, ...] = getattr(ann, "__args__", ())
            elem_cls = args[0] if args else None
            if isinstance(elem_cls, type) and issubclass(elem_cls, Entity):
                result[name] = elem_cls
        return result

    def _to_model_dict(self, aggregate: TAggregate) -> dict[str, Any]:
        """Like to_dict but converts list[Entity] dicts to list[ORM model]."""
        hints = self._hints()
        list_fields = self._list_entity_fields(hints)
        flat = to_dict(aggregate)
        for name, elem_cls in list_fields.items():
            if name not in flat:
                continue
            nested_model_cls = self._mapper.get_model(elem_cls)
            flat[name] = [nested_model_cls(**item) for item in cast(list[Any], flat[name])]
        return flat

    def _from_model(self, model: Any) -> TAggregate:
        hints = self._hints()
        list_fields = self._list_entity_fields(hints)
        init_names = {f.name for f in get_fields(self._aggregate_class) if f.init}
        data: dict[str, Any] = {}
        for name in init_names:
            if not hasattr(model, name):
                continue
            val: Any = getattr(model, name)
            if name in list_fields and isinstance(val, list):
                elem_cls = list_fields[name]
                elem_init_names = {f.name for f in get_fields(cast(type[Entity[Any]], elem_cls)) if f.init}
                data[name] = [
                    from_dict(elem_cls, {k: getattr(item, k) for k in elem_init_names if hasattr(item, k)})
                    for item in cast(list[Any], val)
                ]
            else:
                data[name] = val
        aggregate = self._aggregate_class(**data)
        if hasattr(model, "version"):
            aggregate.version = model.version
        return aggregate

    def save(self, aggregate: TAggregate) -> TId:
        model = self._model_class(**self._to_model_dict(aggregate))
        try:
            self.session.add(model)
            self.session.flush()
        except Exception as exc:
            raise AggregateAlreadyExistError(aggregate) from exc
        aggregate.version = getattr(model, "version", 0)
        return aggregate.id  # pyright: ignore[reportReturnType]

    def delete(self, aggregate: TAggregate) -> None:
        model = self.session.get(self._model_class, aggregate.id.value)
        if model is None:
            raise AggregateDoesNotExistError(aggregate)
        if hasattr(model, "version") and model.version != aggregate.version:
            raise OptimisticLockError(aggregate)
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
        if hasattr(model, "version") and model.version != aggregate.version:
            raise OptimisticLockError(aggregate)
        for key, val in self._to_model_dict(aggregate).items():
            setattr(model, key, val)
        if hasattr(model, "version"):
            model.version += 1
            aggregate.version += 1

    def upsert(self, aggregate: TAggregate) -> None:
        model = self.session.get(self._model_class, aggregate.id.value)
        if model is None:
            self.session.add(self._model_class(**self._to_model_dict(aggregate)))
        else:
            for key, val in self._to_model_dict(aggregate).items():
                setattr(model, key, val)
            if hasattr(model, "version"):
                model.version += 1
