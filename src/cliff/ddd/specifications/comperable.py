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

    __hash__ = type.__hash__  # type: ignore[assignment]

    def __eq__(cls, other: object) -> EqualSpecification | bool:  # type: ignore[override]
        if isinstance(other, type):
            return type.__eq__(cls, other)
        return EqualSpecification(cls, other)  # type: ignore[arg-type]

    def __ne__(cls, other: object) -> NotEqualSpecification | bool:  # type: ignore[override]
        if isinstance(other, type):
            return not type.__eq__(cls, other)
        return NotEqualSpecification(cls, other)  # type: ignore[arg-type]

    def __gt__(cls, other: object) -> GreaterThanSpecification:
        return GreaterThanSpecification(cls, other)  # type: ignore[arg-type]

    def __ge__(cls, other: object) -> GreaterThanEqualSpecification:
        return GreaterThanEqualSpecification(cls, other)  # type: ignore[arg-type]

    def __lt__(cls, other: object) -> LessThanSpecification:
        return LessThanSpecification(cls, other)  # type: ignore[arg-type]

    def __le__(cls, other: object) -> LessThanEqualSpecification:
        return LessThanEqualSpecification(cls, other)  # type: ignore[arg-type]


class ComparableObject(metaclass=ComparableObjectMeta):
    """
    Example usage:

    class MyObject(ComparableObject): ...
    """
