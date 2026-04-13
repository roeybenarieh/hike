from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, Select, and_, not_, or_, select, true
from sqlalchemy.orm import QueryableAttribute

from hike.ddd.specifications import ISpecificationVisitor
from hike.ddd.specifications.specs import (
    AndSpecification,
    BaseLeftRightSpecification,
    EqualSpecification,
    GreaterThanEqualSpecification,
    GreaterThanSpecification,
    LessThanEqualSpecification,
    LessThanSpecification,
    NotEqualSpecification,
    NotSpecification,
    OrSpecification,
)

_EMPTY_FILTER: ColumnElement[Any] = true()


class SQLAlchemyEvaluationSpecificationVisitor(ISpecificationVisitor):
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
