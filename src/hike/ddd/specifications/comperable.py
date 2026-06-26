from abc import ABCMeta

from .specs import (
    EqualSpecification,
    GreaterThanSpecification,
    GreaterThanEqualSpecification,
    LessThanSpecification,
    LessThanEqualSpecification,
    NotEqualSpecification,
)


class ComparableObjectMeta(ABCMeta):
    """Metaclass that makes class-level comparisons return Specification objects.

    Primarily kept for backwards-compatibility and for direct VO class usage.
    Entity fields use the FieldProxy mechanism instead.
    """

    __hash__ = type.__hash__

    def __eq__(cls, other: object) -> EqualSpecification | bool:  # pyright: ignore[reportIncompatibleMethodOverride]
        if isinstance(other, type):
            return type.__eq__(cls, other)
        return EqualSpecification(cls, other)  # pyright: ignore[reportArgumentType]

    def __ne__(cls, other: object) -> NotEqualSpecification | bool:  # pyright: ignore[reportIncompatibleMethodOverride]
        if isinstance(other, type):
            return not type.__eq__(cls, other)
        return NotEqualSpecification(cls, other)  # pyright: ignore[reportArgumentType]

    def __gt__(cls, other: object) -> GreaterThanSpecification:
        return GreaterThanSpecification(cls, other)  # pyright: ignore[reportArgumentType]

    def __ge__(cls, other: object) -> GreaterThanEqualSpecification:
        return GreaterThanEqualSpecification(cls, other)  # pyright: ignore[reportArgumentType]

    def __lt__(cls, other: object) -> LessThanSpecification:
        return LessThanSpecification(cls, other)  # pyright: ignore[reportArgumentType]

    def __le__(cls, other: object) -> LessThanEqualSpecification:
        return LessThanEqualSpecification(cls, other)  # pyright: ignore[reportArgumentType]


class ComparableObject(metaclass=ComparableObjectMeta):
    """
    Example usage:

    class MyObject(ComparableObject): ...
    """
