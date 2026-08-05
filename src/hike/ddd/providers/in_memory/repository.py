from typing import Any, cast

from hike.ddd.aggregate import Aggregate
from hike.ddd.entity import EntityID
from hike.ddd.repository import IRepository, TAggregate, TId, AggregateAlreadyExistError, AggregateDoesNotExistError
from hike.ddd.specifications import ISpecification


class InMemoryRepository(IRepository[TId, dict[Any, Aggregate[Any]], TAggregate]):
    """In-memory repository for testing and prototyping.

    Stores aggregates in the session dict (keyed by raw ``aggregate.id.value``),
    which is managed by ``InMemoryDBContext`` to support rollback.
    """

    def save(self, aggregate: TAggregate) -> TId:
        key = aggregate.id.value
        if key in self.session:
            raise AggregateAlreadyExistError(aggregate)
        self.session[key] = aggregate
        return aggregate.id  # pyright: ignore[reportReturnType]

    def delete(self, aggregate: TAggregate) -> None:
        key = aggregate.id.value
        if key not in self.session:
            raise AggregateDoesNotExistError(aggregate)
        del self.session[key]

    def get_one(self, identifier: EntityID[TId], locked: bool = False) -> TAggregate:
        aggregate = self.session.get(identifier.value)
        if aggregate is None:
            raise AggregateDoesNotExistError(identifier)
        return cast(TAggregate, aggregate)

    def get_many(
            self,
            specification: ISpecification,
            locked: bool = False,
    ) -> list[TAggregate]:
        return [cast(TAggregate, agg) for agg in self.session.values() if specification.is_satisfied(agg)]

    def update(self, aggregate: TAggregate) -> None:
        key = aggregate.id.value
        if key not in self.session:
            raise AggregateDoesNotExistError(aggregate)
        self.session[key] = aggregate

    def upsert(self, aggregate: TAggregate) -> None:
        self.session[aggregate.id.value] = aggregate
