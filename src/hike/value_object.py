import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, ClassVar, Self, cast

from .common import DomainObject


def _cmp_value(other: Any) -> Any:
    if isinstance(other, ValueObject):
        return cast(ValueObject[Any], other).value
    return other


@dataclass(frozen=True)
class ValueObject[V](DomainObject):
    """Immutable value object base class.

    Subclass with the raw value type as a type parameter, annotate ``value``,
    and optionally add ``__validators__`` for reusable validation functions.
    Several built-in validators are provided (``positive``, ``non_negative``,
    ``non_empty``, ``min_value``, ``max_value``, ``between``, ``min_length``,
    ``max_length``, ``matches``):

        class Price(ValueObject[float]):
            value: float
            __validators__ = [positive]

        class Quantity(ValueObject[int]):
            value: int
            __validators__ = [positive]  # same validator, no duplication

    Validators run in MRO order (most-general first) from the base ``__post_init__``.
    Subclasses that need additional logic must call ``super().__post_init__()``
    to ensure ``__validators__`` are executed.

    The type parameter ``V`` lets Pyright enforce that comparison operands are
    the same type (or the same VO type), so ``Price(5) < ""`` is a type error:

        Price(5) < Price(10)   →  bool   ✓
        Price(5) < 10.0        →  bool   ✓
        Price(5) < ""          →  Pyright error: str is not Price | float
    """
    value: V

    __validators__: ClassVar[list[Callable[[Any], None]]] = []

    def __post_init__(self) -> None:
        for klass in reversed(type(self).__mro__):
            for validator in klass.__dict__.get('__validators__', []):
                validator(self.value)

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # Auto-apply @dataclass(frozen=True) to each subclass so the subclass
        # gets its own __init__ with the correctly-typed `value` parameter.
        # eq=False: subclasses inherit __eq__ and __hash__ from ValueObject
        # rather than getting dataclass-generated versions that would override them.
        dataclass(frozen=True, eq=False)(cls)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ValueObject):
            return type(self) is type(other) and self.value == cast(ValueObject[Any], other).value  # type: ignore[reportUnknownArgumentType]
        return self.value == other  # type: ignore[operator]

    def __hash__(self) -> int:
        return hash(self.value)

    def __add__(self, other: Self | V) -> Self:
        return type(self)(self.value + _cmp_value(other))  # type: ignore[operator,return-value]

    def __sub__(self, other: Self | V) -> Self:
        return type(self)(self.value - _cmp_value(other))  # type: ignore[operator,return-value]

    def __mul__(self, other: Self | V) -> Self:
        return type(self)(self.value * _cmp_value(other))  # type: ignore[operator,return-value]

    def __truediv__(self, other: Self | V) -> Self:
        return type(self)(self.value / _cmp_value(other))  # type: ignore[operator,return-value]

    def __lt__(self, other: Self | V) -> bool:
        return self.value < _cmp_value(other)

    def __le__(self, other: Self | V) -> bool:
        return self.value <= _cmp_value(other)

    def __gt__(self, other: Self | V) -> bool:
        return self.value > _cmp_value(other)

    def __ge__(self, other: Self | V) -> bool:
        return self.value >= _cmp_value(other)


# ---------------------------------------------------------------------------
# Built-in validators
# ---------------------------------------------------------------------------

def positive(value: float) -> None:
    """Value must be strictly greater than zero."""
    if value <= 0:
        raise ValueError(f"Value must be positive, got {value!r}")


def non_negative(value: float) -> None:
    """Value must be zero or greater."""
    if value < 0:
        raise ValueError(f"Value must be non-negative, got {value!r}")


def non_empty(value: str) -> None:
    """String value must not be empty or whitespace-only."""
    if not value or not value.strip():
        raise ValueError("Value must not be empty")


def min_value(minimum: float) -> Callable[[float], None]:
    """Factory: value must be >= *minimum*."""
    def _validate(value: float) -> None:
        if value < minimum:
            raise ValueError(f"Value must be >= {minimum}, got {value!r}")
    return _validate


def max_value(maximum: float) -> Callable[[float], None]:
    """Factory: value must be <= *maximum*."""
    def _validate(value: float) -> None:
        if value > maximum:
            raise ValueError(f"Value must be <= {maximum}, got {value!r}")
    return _validate


def between(minimum: float, maximum: float) -> Callable[[float], None]:
    """Factory: value must satisfy *minimum* <= value <= *maximum*."""
    def _validate(value: float) -> None:
        if not (minimum <= value <= maximum):
            raise ValueError(f"Value must be between {minimum} and {maximum}, got {value!r}")
    return _validate


def min_length(minimum: int) -> Callable[[str], None]:
    """Factory: string length must be >= *minimum*."""
    def _validate(value: str) -> None:
        if len(value) < minimum:
            raise ValueError(f"Length must be >= {minimum}, got {len(value)}")
    return _validate


def max_length(maximum: int) -> Callable[[str], None]:
    """Factory: string length must be <= *maximum*."""
    def _validate(value: str) -> None:
        if len(value) > maximum:
            raise ValueError(f"Length must be <= {maximum}, got {len(value)}")
    return _validate


def matches(pattern: str) -> Callable[[str], None]:
    """Factory: string must fully match the given regex *pattern*."""
    compiled = re.compile(pattern)
    def _validate(value: str) -> None:
        if not compiled.fullmatch(value):
            raise ValueError(f"Value {value!r} does not match pattern {pattern!r}")
    return _validate


# ---------------------------------------------------------------------------
# Datetime validators
# ---------------------------------------------------------------------------

def _now(value: datetime) -> datetime:
    return datetime.now(UTC) if value.tzinfo is not None else datetime.now()


def in_past(value: datetime) -> None:
    """Value must be strictly before now."""
    if value >= _now(value):
        raise ValueError(f"Value must be in the past, got {value!r}")


def in_future(value: datetime) -> None:
    """Value must be strictly after now."""
    if value <= _now(value):
        raise ValueError(f"Value must be in the future, got {value!r}")


def not_before(earliest: datetime) -> Callable[[datetime], None]:
    """Factory: value must be >= *earliest*."""
    def _validate(value: datetime) -> None:
        if value < earliest:
            raise ValueError(f"Value must not be before {earliest!r}, got {value!r}")
    return _validate


def not_after(latest: datetime) -> Callable[[datetime], None]:
    """Factory: value must be <= *latest*."""
    def _validate(value: datetime) -> None:
        if value > latest:
            raise ValueError(f"Value must not be after {latest!r}, got {value!r}")
    return _validate


def within_past(delta: timedelta) -> Callable[[datetime], None]:
    """Factory: value must fall within ``[now - delta, now]``."""
    def _validate(value: datetime) -> None:
        now = _now(value)
        if not (now - delta <= value <= now):
            raise ValueError(f"Value must be within the past {delta}, got {value!r}")
    return _validate


def within_future(delta: timedelta) -> Callable[[datetime], None]:
    """Factory: value must fall within ``[now, now + delta]``."""
    def _validate(value: datetime) -> None:
        now = _now(value)
        if not (now <= value <= now + delta):
            raise ValueError(f"Value must be within the next {delta}, got {value!r}")
    return _validate
