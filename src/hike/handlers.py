from abc import abstractmethod
from typing import Any

from hike.domain_event import DomainEvent, EventHandler
from hike.persistence.repository import IRepository
from hike.persistence.uow import UnitOfWork


class CrossAggregateInvariantHandler[TEvent: DomainEvent](EventHandler[TEvent]):
    """Event handler with injected repository and UoW for cross-aggregate invariants.

    Receives the repository and unit-of-work for the *other* aggregate involved
    in the invariant. Subclasses implement ``handle(event)`` and use
    ``self._repo`` / ``self._uow`` to load, check, and update that aggregate.

    Example::

        class EnforceCapacity(CrossAggregateInvariantHandler[ShipDocked]):
            def handle(self, event: ShipDocked) -> None:
                harbor_id = HarborID.from_str(event.harbor_id)
                with self._uow(self._repo):
                    harbor = self._repo.get_one(harbor_id)
                    CapacityRule().check(HarborCtx(harbor=harbor))
                    harbor.receive_ship()
                    self._repo.update(harbor)
                    self._uow.commit()

            def compensate(self, event: ShipDocked) -> None:
                with self._uow(self._repo):
                    harbor = self._repo.get_one(harbor_id)
                    harbor.release_ship()
                    self._repo.update(harbor)
                    self._uow.commit()

        bus.subscribe(ShipDocked, EnforceCapacity(repo=harbor_repo, uow=harbor_uow))
    """

    def __init__(
        self,
        repo: IRepository[Any, Any, Any],
        uow: UnitOfWork[Any],
    ) -> None:
        self._repo = repo
        self._uow = uow

    @abstractmethod
    def handle(self, event: TEvent) -> None: ...

    @abstractmethod
    def compensate(self, event: TEvent) -> None: ...
