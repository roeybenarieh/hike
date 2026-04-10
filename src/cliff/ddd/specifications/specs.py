from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING, final

from .interfaces import ISpecification, IVisitor

if TYPE_CHECKING:
    from cliff.ddd.entity import FieldProxy


##### Composite Specifications ####


class BaseLeftRightSpecification(ISpecification, ABC):
    def __init__(self, left: ISpecification, right: ISpecification) -> None:
        self.left = left
        self.right = right


@final
class AndSpecification(BaseLeftRightSpecification):
    def accept(self, visitor: IVisitor) -> None:
        visitor.visit_and(self)  # type: ignore[arg-type]


@final
class OrSpecification(BaseLeftRightSpecification):
    def accept(self, visitor: IVisitor) -> None:
        visitor.visit_or(self)  # type: ignore[arg-type]


@final
class NotSpecification(ISpecification):
    def __init__(self, spec: ISpecification) -> None:
        self.spec = spec

    def accept(self, visitor: IVisitor) -> None:
        visitor.visit_not(self)  # type: ignore[arg-type]


##### Leaf Specifications #####
class BaseFilterSpecification(ISpecification, ABC):
    def __init__(self, field: FieldProxy, operand: object) -> None:
        self.field = field
        self.operand = operand


@final
class EqualSpecification(BaseFilterSpecification):
    def accept(self, visitor: IVisitor) -> None:
        visitor.visit_equal(self)  # type: ignore[arg-type]


@final
class NotEqualSpecification(BaseFilterSpecification):
    def accept(self, visitor: IVisitor) -> None:
        visitor.visit_not_equal(self)  # type: ignore[arg-type]


@final
class GreaterThanSpecification(BaseFilterSpecification):
    def accept(self, visitor: IVisitor) -> None:
        visitor.visit_greater_than(self)  # type: ignore[arg-type]


@final
class GreaterThanEqualSpecification(BaseFilterSpecification):
    def accept(self, visitor: IVisitor) -> None:
        visitor.visit_greater_than_equal(self)  # type: ignore[arg-type]


@final
class LessThanSpecification(BaseFilterSpecification):
    def accept(self, visitor: IVisitor) -> None:
        visitor.visit_less_than(self)  # type: ignore[arg-type]


@final
class LessThanEqualSpecification(BaseFilterSpecification):
    def accept(self, visitor: IVisitor) -> None:
        visitor.visit_less_than_equal(self)  # type: ignore[arg-type]
