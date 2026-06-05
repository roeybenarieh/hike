from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hike.ddd.entity import DomainField as DomainField  # noqa: F401


class DomainObject: ...


class DomainError(Exception): ...


def __getattr__(name: str) -> object:
    if name == "DomainField":
        from hike.ddd.entity import DomainField
        return DomainField
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
