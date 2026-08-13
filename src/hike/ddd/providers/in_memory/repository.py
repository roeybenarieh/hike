from copy import deepcopy
from typing import Any, cast

from hike.ddd.aggregate import Aggregate
from hike.ddd.entity import EntityID
from hike.ddd.repository import (
    AggregateAlreadyExistError,
    AggregateDoesNotExistError,
    IRepository,
    OptimisticLockError,
    TAggregate,
    TId,
    get_version,
    set_version,
)
from hike.ddd.specifications import ISpecification


class InMemoryRepository(IRepository[TId, dict[Any, Aggregate[Any]], TAggregate]):
    """In-memory repository for testing and prototyping.

    Stores deepcopies of aggregates in the session dict (keyed by raw
    ``aggregate.id.value``) so that in-memory instances are isolated from
    each other, enabling version-based optimistic concurrency checks.
    Session management (rollback support) is provided by ``InMemoryDBContext``.
    """

    def save(self, aggregate: TAggregate) -> TId:
        key = aggregate.id.value
        if key in self.session:
            raise AggregateAlreadyExistError(aggregate)
        copy = deepcopy(aggregate)
        set_version(copy, 0)
        self.session[key] = copy
        set_version(aggregate, 0)
        return aggregate.id  # pyright: ignore[reportReturnType]

    def _delete(self, identifier: EntityID[TId]) -> None:
        key = identifier.value
        if key not in self.session:
            raise AggregateDoesNotExistError(identifier)
        del self.session[key]

    def get_one(self, identifier: EntityID[TId]) -> TAggregate:
        aggregate = self.session.get(identifier.value)
        if aggregate is None:
            raise AggregateDoesNotExistError(identifier)
        return deepcopy(cast(TAggregate, aggregate))

    def get_many(self, specification: ISpecification) -> list[TAggregate]:
        return [
            deepcopy(cast(TAggregate, agg))
            for agg in self.session.values()
            if specification.is_satisfied(agg)
        ]

    def update(self, aggregate: TAggregate) -> None:
        key = aggregate.id.value
        existing = cast(TAggregate | None, self.session.get(key))
        if existing is None:
            raise AggregateDoesNotExistError(aggregate)
        if get_version(existing) != get_version(aggregate):
            raise OptimisticLockError(aggregate)
        copy = deepcopy(aggregate)
        set_version(copy, get_version(aggregate) + 1)
        self.session[key] = copy
        set_version(aggregate, get_version(aggregate) + 1)

    def count(self, specification: ISpecification) -> int:
        return sum(1 for agg in self.session.values() if specification.is_satisfied(agg))

    def upsert(self, aggregate: TAggregate) -> None:
        key = aggregate.id.value
        existing = cast(TAggregate | None, self.session.get(key))
        copy = deepcopy(aggregate)
        set_version(copy, (get_version(existing) + 1) if existing is not None else 0)
        self.session[key] = copy
