from dataclasses import dataclass
from typing import Self, Any

from .common import DomainObject


def _cmp_value(other: Any) -> Any:  # type: ignore[return]
    if isinstance(other, ValueObject):
        return other.value  # type: ignore[return-value]
    return other


@dataclass(frozen=True)
class ValueObject[V](DomainObject):
    """Immutable value object base class.

    Subclass with the raw value type as a type parameter, annotate ``value``,
    and optionally override ``__post_init__`` for validation:

        class Price(ValueObject[float]):
            value: float

            def __post_init__(self) -> None:
                if self.value < 0:
                    raise ValueError("Price cannot be negative")

    The type parameter ``V`` lets Pyright enforce that comparison operands are
    the same type (or the same VO type), so ``Price(5) < ""`` is a type error:

        Price(5) < Price(10)   →  bool   ✓
        Price(5) < 10.0        →  bool   ✓
        Price(5) < ""          →  Pyright error: str is not Price | float
    """
    value: V

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # Auto-apply @dataclass(frozen=True) to each subclass so the subclass
        # gets its own __init__ with the correctly-typed `value` parameter.
        dataclass(frozen=True)(cls)

    def __lt__(self, other: Self | V) -> bool:
        return self.value < _cmp_value(other)

    def __le__(self, other: Self | V) -> bool:
        return self.value <= _cmp_value(other)

    def __gt__(self, other: Self | V) -> bool:
        return self.value > _cmp_value(other)

    def __ge__(self, other: Self | V) -> bool:
        return self.value >= _cmp_value(other)
