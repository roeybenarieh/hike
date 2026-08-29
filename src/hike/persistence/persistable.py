from __future__ import annotations

import dataclasses
from abc import abstractmethod, ABC
from typing import Any, Hashable, Self, cast, final

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


class DataclassPersistable[TId: Hashable](Persistable[TId], ABC):
    """Persistable mixin for plain dataclasses.

    Provides default to_dict, from_dict, and get_init_field_names for any
    plain dataclass — mutable or frozen, with or without non-init fields.
    Subclasses still need to implement get_id().

    For frozen dataclasses, from_dict skips setattr for non-init fields and
    relies on __post_init__ to recompute them (matching frozen semantics).
    """

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(cast(Any, self))}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        dc_fields = dataclasses.fields(cast(Any, cls))
        init_kwargs = {f.name: data[f.name] for f in dc_fields if f.init and f.name in data}
        instance = cls(**init_kwargs)
        params = getattr(cls, "__dataclass_params__", None)
        if not getattr(params, "frozen", False):
            for f in dc_fields:
                if not f.init and f.name in data:
                    setattr(instance, f.name, data[f.name])
        return instance

    @classmethod
    def get_init_field_names(cls) -> tuple[str, ...]:
        return tuple(f.name for f in dataclasses.fields(cast(Any, cls)) if f.init)
