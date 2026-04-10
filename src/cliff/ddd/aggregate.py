from abc import ABC, abstractmethod
from dataclasses import field
from typing import Any, Iterable

from .domain_event import DomainEvent
from .common import DomainError, DomainObject
from .entity import Entity


class Rule[T: DomainObject](ABC):
    """Rule that is met by the BusinessRule

    Unlike invariants - unchanging truth constraints required for data integrity(e.g., "age cannot be negative"),
    Business rules are policies that define operations and can change(e.g., "users over 65 get a discount").
    """

    @abstractmethod
    def is_broken(self, obj: T) -> bool: ...

    def raise_on_broken_rule(self, obj: T) -> None:
        if self.is_broken(obj):
            raise RuleBrokenError(self)


class RuleBrokenError(DomainError):
    def __init__(self, rule: Rule[Any]):
        self.broken_rule: Rule[Any] = rule


class Aggregate(Entity):
    """Base class for DDD aggregate roots.

    Adds domain event collection and business rule checking on top of Entity.
    Declare ``_events`` with ``init=False`` so it is never passed as a
    constructor argument:

        class Order(Aggregate):
            id: UUID = field(default_factory=uuid4)
            total: Money
    """

    _events: list[DomainEvent] = field(  # pyright: ignore[reportUnknownVariableType]
        default_factory=list, init=False, repr=False
    )

    def get_events(self) -> list[DomainEvent]:
        return list(self._events)

    def clear_events(self) -> None:
        self._events.clear()

    def check_invariants(self, rules: Iterable[Rule[Any]]) -> None:
        for rule in rules:
            rule.raise_on_broken_rule(self)
