from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, final, Iterable, Iterator

if TYPE_CHECKING:
    from .specs import (
        AndSpecification,
        EqualSpecification,
        GreaterThanEqualSpecification,
        GreaterThanSpecification,
        LessThanEqualSpecification,
        LessThanSpecification,
        NotEqualSpecification,
        NotSpecification,
        OrSpecification,
        RegexSpecification,
    )

# TODO: add a NoneSpecification
# TODO: support specifications between two TerminalFieldProxy
class ISpecification(ABC):
    def __and__(self, other: ISpecification) -> AndSpecification:
        from .specs import AndSpecification

        return AndSpecification(self, other)

    def __or__(self, other: ISpecification) -> OrSpecification:
        from .specs import OrSpecification

        return OrSpecification(self, other)

    def __invert__(self) -> NotSpecification:
        from .specs import NotSpecification

        return NotSpecification(self)

    @abstractmethod
    def accept(self, visitor: ISpecificationVisitor) -> None: ...

    @final
    def filter[TObj](self, objects: Iterable[TObj]) -> Iterator[TObj]:
        return filter(self.is_satisfied, objects)

    @final
    def is_satisfied(self, obj: object) -> bool:
        from hike.persistence.providers.in_memory import InMemoryEvaluationSpecificationVisitor

        visitor = InMemoryEvaluationSpecificationVisitor(obj)
        self.accept(visitor)
        return visitor.result


class ISpecificationVisitor(ABC):
    @abstractmethod
    def visit_and(self, spec: AndSpecification) -> None: ...

    @abstractmethod
    def visit_or(self, spec: OrSpecification) -> None: ...

    @abstractmethod
    def visit_not(self, spec: NotSpecification) -> None: ...

    @abstractmethod
    def visit_equal(self, spec: EqualSpecification) -> None: ...

    @abstractmethod
    def visit_not_equal(self, spec: NotEqualSpecification) -> None: ...

    @abstractmethod
    def visit_greater_than(self, spec: GreaterThanSpecification) -> None: ...

    @abstractmethod
    def visit_greater_than_equal(self, spec: GreaterThanEqualSpecification) -> None: ...

    @abstractmethod
    def visit_less_than(self, spec: LessThanSpecification) -> None: ...

    @abstractmethod
    def visit_less_than_equal(self, spec: LessThanEqualSpecification) -> None: ...

    @abstractmethod
    def visit_regex(self, spec: RegexSpecification) -> None: ...
