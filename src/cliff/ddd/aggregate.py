from typing import Generic, Hashable, Iterable, TypeVar
from uuid import UUID
from abc import ABC, abstractmethod

from cliff.ddd.entity import Entity, EntityID

from .domain_event import DomainEvent
from .common import DomainError, DomainObject


class Rule[obj: DomainObject](ABC):
    """Rule that is met by the BusinessRule

    Unlike invariants - unchanging truth constraints required for data integrity(e.g., "age cannot be negative"),
    Business rules are policies that define operations and can change(e.g., "users over 65 get a discount").
    """

    @abstractmethod
    def is_broken(self, obj: obj) -> bool: ...

    def raise_on_broken_rule(self, obj: obj):
        if self.is_broken(obj):
            raise RuleBrokenError(self)


class RuleBrokenError(DomainError):
    def __init__(self, rule: Rule):
        self.broken_rule = rule


class AggregateID[TId: Hashable](EntityID[TId]): ...


class AggregateUUID(AggregateID[UUID]): ...


TId = TypeVar("TId", bound=AggregateID)


class Aggregate(Entity[TId]):
    _events: list[DomainEvent]

    def get_events(self) -> DomainEvent: ...
    def clear_events(self) -> None: ...

    # TODO: since invariants are not reused, maybe remove this.
    def check_invariants(self, rules: Iterable[Rule]):
        for rule in rules:
            rule.raise_on_broken_rule(self)
