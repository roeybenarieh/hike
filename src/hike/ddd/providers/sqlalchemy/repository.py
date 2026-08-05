from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from hike.ddd.entity import EntityID, get_fields, to_dict
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

    def _from_model(self, model: Any) -> TAggregate:
        init_names = {f.name for f in get_fields(self._aggregate_class) if f.init}
        data = {k: getattr(model, k) for k in init_names if hasattr(model, k)}
        aggregate = self._aggregate_class(**data)
        if hasattr(model, "version"):
            aggregate.version = model.version
        return aggregate

    def save(self, aggregate: TAggregate) -> TId:
        model = self._model_class(**to_dict(aggregate))
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
        for key, val in to_dict(aggregate).items():
            setattr(model, key, val)
        if hasattr(model, "version"):
            model.version += 1
            aggregate.version += 1

    def upsert(self, aggregate: TAggregate) -> None:
        model = self.session.get(self._model_class, aggregate.id.value)
        if model is None:
            self.session.add(self._model_class(**to_dict(aggregate)))
        else:
            for key, val in to_dict(aggregate).items():
                setattr(model, key, val)
            if hasattr(model, "version"):
                model.version += 1
