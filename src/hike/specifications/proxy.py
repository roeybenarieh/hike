from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Protocol

if TYPE_CHECKING:
    from .specs import (
        EqualSpecification,
        GreaterThanEqualSpecification,
        GreaterThanSpecification,
        LessThanEqualSpecification,
        LessThanSpecification,
        NotEqualSpecification,
        RegexSpecification,
    )

# Given the owner class and a field name, returns (child_field_type, child_owner_class)
# if the name resolves to a chainable field, or None if it does not.
FieldResolver = Callable[[type, str], "tuple[type, type] | None"]


class FieldPath(Protocol):
    """Minimal interface consumed by specs and visitors: a chain of field-name segments."""

    @property
    def path(self) -> list[str]: ...


class FieldByName:
    """Concrete FieldPath for callers who know a field by name rather than via a proxy.

    Use when building specs programmatically (e.g. inside framework internals)
    instead of through the ComparableObject / TerminalFieldProxy DSL.
    """

    def __init__(self, *names: str) -> None:
        self._path = list(names)

    @property
    def path(self) -> list[str]:
        return self._path


class TerminalFieldProxy:
    """Class-level proxy for a leaf field that supports specification building.

    Comparison operators produce ``Specification`` objects::

        Boat.price < 100        →  LessThanSpecification
        Boat.price == 50        →  EqualSpecification
        (Boat.price >= 10) & (Boat.price < 100)   →  AndSpecification

    Does NOT support attribute chaining.  Use ``FieldProxy`` for fields whose
    type can be further traversed.
    """

    def __init__(
            self,
            field_name: str,
            field_type: type,
            owner_class: type,
            parent: FieldProxy | None = None,
    ) -> None:
        self.field_name = field_name
        self.field_type = field_type
        self.owner_class = owner_class
        self.parent = parent

    @property
    def path(self) -> list[str]:
        """Full field-name chain from root to leaf, e.g. ``['engine', 'price']``."""
        parts: list[str] = []
        node: TerminalFieldProxy | None = self
        while node is not None:
            parts.append(node.field_name)
            node = node.parent
        parts.reverse()
        return parts

    @property
    def root(self) -> TerminalFieldProxy:
        """The root proxy in the chain."""
        node: TerminalFieldProxy = self
        while node.parent is not None:
            node = node.parent
        return node

    def _reject_proxy_operand(self, other: object) -> None:
        if isinstance(other, TerminalFieldProxy):
            raise TypeError(
                f"Field-to-field comparisons are not supported. "
                f"Use a scalar value as the operand, not {other!r}."
            )

    def __eq__(self, other: object) -> EqualSpecification:  # pyright: ignore[reportIncompatibleMethodOverride]
        from .specs import EqualSpecification
        self._reject_proxy_operand(other)
        return EqualSpecification(self, other)

    def __ne__(self, other: object) -> NotEqualSpecification:  # pyright: ignore[reportIncompatibleMethodOverride]
        from .specs import NotEqualSpecification
        self._reject_proxy_operand(other)
        return NotEqualSpecification(self, other)

    def __lt__(self, other: object) -> LessThanSpecification:
        from .specs import LessThanSpecification
        self._reject_proxy_operand(other)
        return LessThanSpecification(self, other)

    def __le__(self, other: object) -> LessThanEqualSpecification:
        from .specs import LessThanEqualSpecification
        self._reject_proxy_operand(other)
        return LessThanEqualSpecification(self, other)

    def __gt__(self, other: object) -> GreaterThanSpecification:
        from .specs import GreaterThanSpecification
        self._reject_proxy_operand(other)
        return GreaterThanSpecification(self, other)

    def __ge__(self, other: object) -> GreaterThanEqualSpecification:
        from .specs import GreaterThanEqualSpecification
        self._reject_proxy_operand(other)
        return GreaterThanEqualSpecification(self, other)

    def matches(self, pattern: str, *, case_insensitive: bool = False) -> RegexSpecification:
        """Return a regex specification for this field (RE2/POSIX ERE dialect).

        Requires ``hike[regex]``.
        """
        from .specs import RegexSpecification
        return RegexSpecification(self, pattern, case_insensitive=case_insensitive)

    def __hash__(self) -> int:
        return hash((self.field_name, self.owner_class))

    def __repr__(self) -> str:
        return f"{self.root.owner_class.__name__}.{'.'.join(self.path)}"


class FieldProxy(TerminalFieldProxy):
    """Class-level proxy for a chainable field.

    Extends ``TerminalFieldProxy`` with attribute access for traversing nested fields::

        Boat.engine.price > 1_000   →  GreaterThanSpecification with path ["engine", "price"]

    The ``path`` property returns the full list of field names from root to leaf.
    Visitor implementations use it to traverse the object graph during evaluation.

    Chaining is powered by a ``FieldResolver`` injected at construction time.
    The resolver is the only component that knows how to look up child field types;
    ``FieldProxy`` itself is fully decoupled from any specific field system.
    """

    def __init__(
            self,
            field_name: str,
            field_type: type,
            owner_class: type,
            parent: FieldProxy | None = None,
            *,
            resolver: FieldResolver | None = None,
    ) -> None:
        super().__init__(field_name, field_type, owner_class, parent=parent)
        self._resolver = resolver

    def __getattr__(self, name: str) -> FieldProxy:
        """Enable chaining: ``Boat.engine.price`` returns a nested ``FieldProxy``."""
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        if self._resolver is not None:
            result = self._resolver(self.field_type, name)
            if result is not None:
                child_field_type, child_owner_class = result
                return FieldProxy(name, child_field_type, child_owner_class, parent=self, resolver=self._resolver)
        raise AttributeError(
            f"'{self.field_type.__name__}' has no chainable field '{name}'."
        )
