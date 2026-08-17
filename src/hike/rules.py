from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, overload

from .common import DomainError, DomainObject

if TYPE_CHECKING:
    from .specifications.interfaces import ISpecification


class _AnyRule(ABC):
    """Internal base shared by Rule and CrossAggregateRule."""


class Rule[T: DomainObject](_AnyRule, ABC):
    """Abstract business rule.

    ``is_broken`` returns ``True`` when the rule is violated.
    ``raise_on_broken_rule`` raises ``RuleBrokenError`` if broken.
    """

    @abstractmethod
    def is_broken(self, obj: T) -> bool: ...

    def raise_on_broken_rule(self, obj: T) -> None:
        if self.is_broken(obj):
            raise RuleBrokenError(self)


class CrossAggregateRule[T](_AnyRule, ABC):
    """A rule whose invariant spans more than one aggregate.

    ``T`` is a context object (typically a ``dataclass``) holding all
    aggregates involved.  Must be checked explicitly — ``@command`` never
    auto-checks cross-aggregate rules.
    """

    @abstractmethod
    def is_broken(self, context: T) -> bool: ...

    def check(self, context: T) -> None:
        """Raise ``RuleBrokenError`` if the rule is violated."""
        if self.is_broken(context):
            raise RuleBrokenError(self)


class RuleBrokenError(DomainError):
    def __init__(self, broken_rule: _AnyRule):
        self.broken_rule: _AnyRule = broken_rule


class FunctionalRule[T: DomainObject](Rule[T]):
    """A ``Rule`` defined by a predicate function, without requiring subclassing.

    Prefer the ``rule()`` decorator for terse definitions::

        @rule
        def price_in_range(boat: Boat) -> bool:
            return not (1_000 <= boat.price.value <= 500_000)

    For cross-type reuse, pair with a ``Protocol``::

        class HasValue(Protocol):
            value: int | float

        @rule
        def positive(obj: HasValue) -> bool:
            return obj.value <= 0
    """

    def __init__(self, predicate: Callable[[T], bool], message: str = "") -> None:
        self._predicate = predicate
        self._message = message

    def is_broken(self, obj: T) -> bool:
        return self._predicate(obj)

    def __repr__(self) -> str:
        return f"FunctionalRule({self._message!r})"


class SpecificationRule[T: DomainObject](Rule[T]):
    """A ``Rule`` that delegates to an ``ISpecification``.

    The rule is broken when the specification is *not* satisfied.
    Evaluation uses the in-memory evaluator via ``ISpecification.is_satisfied``.
    """

    def __init__(self, spec: "ISpecification") -> None:
        self._spec = spec

    def is_broken(self, obj: T) -> bool:
        return not self._spec.is_satisfied(obj)


@overload
def rule[T: DomainObject](predicate: Callable[[T], bool]) -> FunctionalRule[T]: ...


@overload
def rule[T: DomainObject](*, message: str) -> Callable[[Callable[[T], bool]], FunctionalRule[T]]: ...


def rule[T: DomainObject](
    predicate: Callable[[T], bool] | None = None,
    *,
    message: str = "",
) -> FunctionalRule[T] | Callable[[Callable[[T], bool]], FunctionalRule[T]]:
    """Decorator that turns a predicate function into a reusable ``Rule``.

    The function name is used as the message when none is given::

        @rule
        def price_in_range(boat: Boat) -> bool:
            return not (1_000 <= boat.price.value <= 500_000)

        @rule(message="Price must be between €1,000 and €500,000")
        def price_in_range(boat: Boat) -> bool:
            return not (1_000 <= boat.price.value <= 500_000)
    """
    def _make(fn: Callable[[T], bool]) -> FunctionalRule[T]:
        return FunctionalRule(fn, message or getattr(fn, '__name__', ''))
    if predicate is not None:
        return _make(predicate)
    return _make
