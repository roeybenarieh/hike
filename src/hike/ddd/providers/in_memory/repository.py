from typing import Any

from hike.ddd.aggregate import Aggregate
from hike.ddd.repository import IRepository, TId, AggregateAlreadyExistError, AggregateDoesNotExistError
from hike.ddd.specifications import ISpecification


class InMemoryRepository(IRepository[TId, dict[Any, "Aggregate[Any]"]]):
    """In-memory repository for testing and prototyping.

    Stores aggregates in the session dict (keyed by raw ``aggregate.id.value``),
    which is managed by ``InMemoryDBContext`` to support rollback.
    """

    def save(self, aggregate: Aggregate[TId]) -> TId:
        key = aggregate.id.value
        if key in self.session:
            raise AggregateAlreadyExistError(aggregate)
        self.session[key] = aggregate
        return aggregate.id  # pyright: ignore[reportReturnType]

    def delete(self, aggregate: Aggregate[TId]) -> None:
        key = aggregate.id.value
        if key not in self.session:
            raise AggregateDoesNotExistError(aggregate)
        del self.session[key]

    def get_one(self, identifier: TId, locked: bool = False) -> Aggregate[TId]:
        aggregate = self.session.get(identifier)
        if aggregate is None:
            raise AggregateDoesNotExistError(identifier)
        return aggregate

    def get_many(
            self,
            specification: ISpecification,
            locked: bool = False,
    ) -> list[Aggregate[TId]]:
        return [agg for agg in self.session.values() if specification.is_satisfied(agg)]

    def update(self, aggregate: Aggregate[TId]) -> None:
        key = aggregate.id.value
        if key not in self.session:
            raise AggregateDoesNotExistError(aggregate)
        self.session[key] = aggregate

    def upsert(self, aggregate: Aggregate[TId]) -> None:
        self.session[aggregate.id.value] = aggregate
