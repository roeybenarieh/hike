from __future__ import annotations

from abc import abstractmethod, ABC
from typing import Any, Hashable, Self, final

_HIKE_VERSION = "__hike_version__"


class Persistable[TId: Hashable](ABC):
    """Persistence identity contract. Not a ``DomainObject``.

    Declares the full interface every persistable object must satisfy:
    a ``get_id()`` method, and serialization helpers ``to_dict``, ``from_dict``,
    and ``get_init_field_names``.
    """

    @abstractmethod
    def get_id(self) -> TId:
        """Return the persistence identity of this object."""

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (ValueObjects flattened to raw values)."""

    @classmethod
    @abstractmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Reconstruct an instance from a plain dict produced by ``to_dict``."""

    @classmethod
    @abstractmethod
    def get_init_field_names(cls) -> tuple[str, ...]:
        """Return the names of fields that are included in ``__init__``."""

    @final
    def get_version(self) -> int:
        """Return the optimistic-concurrency version set by the repository. 0 if never persisted."""
        return self.__dict__.get(_HIKE_VERSION, 0)

    @final
    def set_version(self, version: int) -> None:
        """Set the repository-managed optimistic-concurrency version. Not intended for domain code."""
        vars(self)[_HIKE_VERSION] = version

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        return self.get_id() == other.get_id()  # type: ignore[union-attr]

    def __hash__(self) -> int:
        return hash(self.get_id())
