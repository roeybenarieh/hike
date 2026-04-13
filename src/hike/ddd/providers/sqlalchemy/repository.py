from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from hike.ddd.aggregate import Aggregate
from hike.ddd.entity import get_fields, to_dict
from hike.ddd.repository import (
    AggregateAlreadyExistError,
    AggregateDoesNotExistError,
    IRepository,
    TId,
)
from hike.ddd.specifications import ISpecification

from .visitor import SQLAlchemyEvaluationSpecificationVisitor


class SQLAlchemyRepository(IRepository[TId, Session]):
    """Generic SQLAlchemy ORM repository.

    ``model_class`` must be a SQLAlchemy mapped class whose column names match
    the aggregate's dataclass field names after ``to_dict`` flattening.
    ``get_many`` translates the specification tree into a SQL WHERE clause via
    ``SQLAlchemyEvaluationVisitor``.  ``locked=True`` adds ``FOR UPDATE``.

    Install with: ``pip install hike[sqlalchemy]``
    """

    def __init__(
            self,
            model_class: type[Any],
            aggregate_class: type[Aggregate[TId]],
    ) -> None:
        super().__init__()
        self._model_class = model_class
        self._aggregate_class = aggregate_class

    def _from_model(self, model: Any) -> Aggregate[TId]:
        init_names = {f.name for f in get_fields(self._aggregate_class) if f.init}
        data = {k: getattr(model, k) for k in init_names if hasattr(model, k)}
        return self._aggregate_class(**data)

    def save(self, aggregate: Aggregate[TId]) -> TId:
        model = self._model_class(**to_dict(aggregate))
        try:
            self.session.add(model)
            self.session.flush()
        except Exception as exc:
            raise AggregateAlreadyExistError(aggregate) from exc
        return aggregate.id  # pyright: ignore[reportReturnType]

    def delete(self, aggregate: Aggregate[TId]) -> None:
        model = self.session.get(self._model_class, aggregate.id.value)
        if model is None:
            raise AggregateDoesNotExistError(aggregate)
        self.session.delete(model)

    def get_one(self, identifier: TId, locked: bool = False) -> Aggregate[TId]:
        model = self.session.get(
            self._model_class,
            identifier,
            with_for_update=True if locked else None,
        )
        if model is None:
            raise AggregateDoesNotExistError(identifier)
        return self._from_model(model)

    def get_many(
            self,
            specification: ISpecification,
            locked: bool = False,
    ) -> list[Aggregate[TId]]:
        visitor = SQLAlchemyEvaluationSpecificationVisitor(self._model_class)
        specification.accept(visitor)
        stmt = visitor.result()
        if locked:
            stmt = stmt.with_for_update()
        rows = self.session.scalars(stmt).all()
        return [self._from_model(row) for row in rows]

    def update(self, aggregate: Aggregate[TId]) -> None:
        model = self.session.get(self._model_class, aggregate.id.value)
        if model is None:
            raise AggregateDoesNotExistError(aggregate)
        for key, val in to_dict(aggregate).items():
            setattr(model, key, val)

    def upsert(self, aggregate: Aggregate[TId]) -> None:
        model = self.session.get(self._model_class, aggregate.id.value)
        if model is None:
            self.session.add(self._model_class(**to_dict(aggregate)))
        else:
            for key, val in to_dict(aggregate).items():
                setattr(model, key, val)
