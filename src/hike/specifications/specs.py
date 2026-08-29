from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING, cast, final

import re2 as _re2

from .interfaces import ISpecification, ISpecificationVisitor

if TYPE_CHECKING:
    from .proxy import FieldPath


##### Composite Specifications ####


class BaseLeftRightSpecification(ISpecification, ABC):
    def __init__(self, left: ISpecification, right: ISpecification) -> None:
        self.left = left
        self.right = right


@final
class AndSpecification(BaseLeftRightSpecification):
    def accept(self, visitor: ISpecificationVisitor) -> None:
        visitor.visit_and(self)


@final
class OrSpecification(BaseLeftRightSpecification):
    def accept(self, visitor: ISpecificationVisitor) -> None:
        visitor.visit_or(self)


@final
class NotSpecification(ISpecification):
    def __init__(self, spec: ISpecification) -> None:
        self.spec = spec

    def accept(self, visitor: ISpecificationVisitor) -> None:
        visitor.visit_not(self)


##### Leaf Specifications #####
class BaseFilterSpecification(ISpecification, ABC):
    def __init__(self, field: FieldPath, operand: object) -> None:
        self.field = field
        self.operand = operand


@final
class EqualSpecification(BaseFilterSpecification):
    def accept(self, visitor: ISpecificationVisitor) -> None:
        visitor.visit_equal(self)


@final
class NotEqualSpecification(BaseFilterSpecification):
    def accept(self, visitor: ISpecificationVisitor) -> None:
        visitor.visit_not_equal(self)


@final
class GreaterThanSpecification(BaseFilterSpecification):
    def accept(self, visitor: ISpecificationVisitor) -> None:
        visitor.visit_greater_than(self)


@final
class GreaterThanEqualSpecification(BaseFilterSpecification):
    def accept(self, visitor: ISpecificationVisitor) -> None:
        visitor.visit_greater_than_equal(self)


@final
class LessThanSpecification(BaseFilterSpecification):
    def accept(self, visitor: ISpecificationVisitor) -> None:
        visitor.visit_less_than(self)


@final
class LessThanEqualSpecification(BaseFilterSpecification):
    def accept(self, visitor: ISpecificationVisitor) -> None:
        visitor.visit_less_than_equal(self)


@final
class RegexSpecification(BaseFilterSpecification):
    """Filter by an RE2 pattern evaluated identically across all backends.

    **Pattern dialect**

    Patterns must be valid RE2 expressions.  RE2 rejects constructs it does
    not support — lookaheads, lookbehinds, backreferences, and word-boundary
    assertions — by raising ``re2.error`` at construction time.

    Both POSIX named classes and Perl shorthands are accepted::

        [[:digit:]] / \\d  — digits 0-9
        [[:alpha:]]        — ASCII letters A-Za-z only
        [[:alnum:]] / \\w  — ASCII letters + digits (\\w includes _)
        [[:space:]] / \\s  — whitespace
        [[:upper:]]        — uppercase ASCII letters A-Z
        [[:lower:]]        — lowercase ASCII letters a-z

    **ASCII-only POSIX classes**

    RE2 treats POSIX character classes as ASCII-only: ``[[:alpha:]]`` matches
    only A-Za-z.  All supported database backends match this behaviour.

    **Case sensitivity**

    ``^`` and ``$`` match string boundaries only (not line boundaries),
    consistent with all supported database backends.
    """

    def __init__(
        self,
        field: FieldPath,
        pattern: str,
        case_insensitive: bool = False,
    ) -> None:
        opts = _re2.Options()
        opts.case_sensitive = not case_insensitive
        self._compiled = _re2.compile(pattern, opts)

        super().__init__(field, pattern)
        self.case_insensitive = case_insensitive

    @property
    def pattern(self) -> str:
        return cast(str, self.operand)

    def search(self, value: str) -> bool:
        """Return ``True`` if *value* contains a match for this pattern."""
        return self._compiled.search(value) is not None

    def accept(self, visitor: ISpecificationVisitor) -> None:
        visitor.visit_regex(self)
