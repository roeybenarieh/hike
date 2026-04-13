from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, Select, and_, not_, or_, select, true
from sqlalchemy.orm import QueryableAttribute, Session, sessionmaker

from hike.ddd.aggregate import Aggregate
from hike.ddd.entity import to_dict, get_fields
from hike.ddd.repository import (
    AggregateAlreadyExistError,
    AggregateDoesNotExistError,
    IRepository,
    TId,
)
from hike.ddd.specifications import ISpecification, IVisitor
from hike.ddd.specifications.specs import (
    AndSpecification,
    EqualSpecification,
    GreaterThanEqualSpecification,
    GreaterThanSpecification,
    LessThanEqualSpecification,
    LessThanSpecification,
    NotEqualSpecification,
    NotSpecification,
    OrSpecification,
    BaseLeftRightSpecification,
)
from hike.ddd.uow import DBContext

_EMPTY_FILTER: ColumnElement[Any] = true()


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class SQLAlchemyRepository(IRepository[TId, Session]):
    """Generic SQLAlchemy ORM repository.

    ``model_class`` must be a SQLAlchemy mapped class whose column names match
    the aggregate's dataclass field names after ``to_dict`` flattening.
    ``get_many`` translates the specification tree into a SQL WHERE clause via
    ``SQLAlchemyEvaluationVisitor``.  ``locked=True`` adds ``FOR UPDATE``.

    Install with: ``pip install cliff[sqlalchemy]``
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
        visitor = SQLAlchemyEvaluationVisitor(self._model_class)
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


# ---------------------------------------------------------------------------
# DBContext
# ---------------------------------------------------------------------------


class SQLAlchemyDBContext(DBContext[Session]):
    """DBContext backed by a SQLAlchemy Session.

    Usage::

        factory = sessionmaker(bind=engine)
        ctx = SQLAlchemyDBContext(factory)
        with UnitOfWork(repos, ctx):
            repo.add(aggregate)
            uow.commit()
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__()
        self._session_factory = session_factory

    def begin(self) -> None:
        self._session = self._session_factory()

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def close(self) -> None:
        self.session.close()
        self._session = None


# ---------------------------------------------------------------------------
# Evaluation visitor
# ---------------------------------------------------------------------------


class SQLAlchemyEvaluationVisitor(IVisitor):
    """Translates a specification tree into a SQLAlchemy SELECT with WHERE filters."""

    def __init__(self, table_object: type[QueryableAttribute[Any]]) -> None:
        self.table_object = table_object
        self.filters: ColumnElement[Any] = _EMPTY_FILTER

    def result(self) -> Select[Any]:
        return select(self.table_object).filter(self.filters)

    def _pop_filters(self) -> ColumnElement[Any]:
        filters = self.filters
        self.filters = _EMPTY_FILTER
        return filters

    def _visit_left_right_spec(
            self, spec: BaseLeftRightSpecification
    ) -> tuple[ColumnElement[Any], ColumnElement[Any]]:
        spec.left.accept(self)
        left = self._pop_filters()
        spec.right.accept(self)
        return left, self._pop_filters()

    def _visit_filter(
            self, spec: EqualSpecification | NotEqualSpecification | GreaterThanSpecification |
                        GreaterThanEqualSpecification | LessThanSpecification | LessThanEqualSpecification
    ) -> tuple[QueryableAttribute[Any], Any]:
        return getattr(self.table_object, spec.field.field_name), spec.operand

    def visit_and(self, spec: AndSpecification) -> None:
        left, right = self._visit_left_right_spec(spec)
        self.filters = and_(left, right)

    def visit_or(self, spec: OrSpecification) -> None:
        left, right = self._visit_left_right_spec(spec)
        self.filters = or_(left, right)

    def visit_not(self, spec: NotSpecification) -> None:
        spec.spec.accept(self)
        self.filters = not_(self.filters)

    def visit_equal(self, spec: EqualSpecification) -> None:
        col, val = self._visit_filter(spec)
        self.filters = col == val

    def visit_not_equal(self, spec: NotEqualSpecification) -> None:
        col, val = self._visit_filter(spec)
        self.filters = col != val

    def visit_greater_than(self, spec: GreaterThanSpecification) -> None:
        col, val = self._visit_filter(spec)
        self.filters = col > val

    def visit_greater_than_equal(self, spec: GreaterThanEqualSpecification) -> None:
        col, val = self._visit_filter(spec)
        self.filters = col >= val

    def visit_less_than(self, spec: LessThanSpecification) -> None:
        col, val = self._visit_filter(spec)
        self.filters = col < val

    def visit_less_than_equal(self, spec: LessThanEqualSpecification) -> None:
        col, val = self._visit_filter(spec)
        self.filters = col <= val
