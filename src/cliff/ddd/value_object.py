from typing import Any

from dataclasses import dataclass
from .common import DomainObject


@dataclass(frozen=True)
class ValueObject(DomainObject):
    """
    Inherit from this object and decorate it with a frozen dataclass.
    use __post_init__ for value validation.
    """

    value: Any
    # check for value object implementations

    def __hash__(self) -> int:
        return hash(self.value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, type(self)):
            return self.value == self.value
        return self.value == other
