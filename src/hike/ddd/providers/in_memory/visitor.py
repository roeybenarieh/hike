from __future__ import annotations

from typing import Any, cast

from hike.ddd.specifications.interfaces import ISpecificationVisitor
from hike.ddd.specifications.specs import (
    AndSpecification,
    BaseFilterSpecification,
    NotSpecification,
    OrSpecification,
)
from hike.ddd.value_object import ValueObject


class InMemoryEvaluationSpecificationVisitor(ISpecificationVisitor):
    """Evaluates a specification tree against a single in-memory object.

    Usage::

        visitor = InMemoryEvaluationVisitor(my_entity)
        spec.accept(visitor)
        if visitor.result:
            ...

    The visitor reads fields via ``getattr`` and unwraps ``ValueObject``
    instances to their ``.value`` automatically.
    """

    def __init__(self, obj: object) -> None:
        self._obj = obj
        self.result: bool = True

    def _field_value(self, spec: BaseFilterSpecification) -> Any:
        val: Any = self._obj
        for name in spec.field.path:
            val = getattr(val, name)
        if isinstance(val, ValueObject):
            return cast(Any, val).value
        return val

    def _pop_result(self) -> bool:
        r = self.result
        self.result = False
        return r

    def visit_and(self, spec: AndSpecification) -> None:
        spec.left.accept(self)
        left = self._pop_result()
        spec.right.accept(self)
        self.result = left and self.result

    def visit_or(self, spec: OrSpecification) -> None:
        spec.left.accept(self)
        left = self._pop_result()
        spec.right.accept(self)
        self.result = left or self.result

    def visit_not(self, spec: NotSpecification) -> None:
        spec.spec.accept(self)
        self.result = not self.result

    def visit_equal(self, spec: BaseFilterSpecification) -> None:
        self.result = self._field_value(spec) == spec.operand

    def visit_not_equal(self, spec: BaseFilterSpecification) -> None:
        self.result = self._field_value(spec) != spec.operand

    def visit_greater_than(self, spec: BaseFilterSpecification) -> None:
        self.result = self._field_value(spec) > spec.operand

    def visit_greater_than_equal(self, spec: BaseFilterSpecification) -> None:
        self.result = self._field_value(spec) >= spec.operand

    def visit_less_than(self, spec: BaseFilterSpecification) -> None:
        self.result = self._field_value(spec) < spec.operand

    def visit_less_than_equal(self, spec: BaseFilterSpecification) -> None:
        self.result = self._field_value(spec) <= spec.operand
