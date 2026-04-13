from __future__ import annotations

from typing import Any

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


class MongoDBEvaluationSpecificationVisitor(ISpecificationVisitor):
    """Translates a specification tree into a pymongo filter dict.

    Usage::

        visitor = MongoDBEvaluationVisitor()
        spec.accept(visitor)
        documents = collection.find(visitor.filters)

    The filter dict is available as ``visitor.filters`` to pass directly to
    any pymongo collection method.
    """

    def __init__(self) -> None:
        self.filters: dict[str, Any] = {}

    def _pop_filters(self) -> dict[str, Any]:
        f = self.filters
        self.filters = {}
        return f

    def _visit_left_right_spec(
            self, spec: BaseLeftRightSpecification
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        spec.left.accept(self)
        left = self._pop_filters()
        spec.right.accept(self)
        return left, self._pop_filters()

    def visit_and(self, spec: AndSpecification) -> None:
        left, right = self._visit_left_right_spec(spec)
        self.filters = {"$and": [left, right]}

    def visit_or(self, spec: OrSpecification) -> None:
        left, right = self._visit_left_right_spec(spec)
        self.filters = {"$or": [left, right]}

    def visit_not(self, spec: NotSpecification) -> None:
        spec.spec.accept(self)
        inner = self._pop_filters()
        self.filters = {"$nor": [inner]}

    def visit_equal(self, spec: EqualSpecification) -> None:
        self.filters = {spec.field.field_name: {"$eq": spec.operand}}

    def visit_not_equal(self, spec: NotEqualSpecification) -> None:
        self.filters = {spec.field.field_name: {"$ne": spec.operand}}

    def visit_greater_than(self, spec: GreaterThanSpecification) -> None:
        self.filters = {spec.field.field_name: {"$gt": spec.operand}}

    def visit_greater_than_equal(self, spec: GreaterThanEqualSpecification) -> None:
        self.filters = {spec.field.field_name: {"$gte": spec.operand}}

    def visit_less_than(self, spec: LessThanSpecification) -> None:
        self.filters = {spec.field.field_name: {"$lt": spec.operand}}

    def visit_less_than_equal(self, spec: LessThanEqualSpecification) -> None:
        self.filters = {spec.field.field_name: {"$lte": spec.operand}}
