from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import UUID

from hike.aggregate import UuidAggregate
from hike.domain_event import DomainEvent, EventBus, deserialize_event, serialize_event
from hike.persistence.repository import IRepository


class OutboxRecord(UuidAggregate):
    """Infrastructure aggregate representing one pending event row in the outbox table.

    Records are created by ``IOutboxRepository.save_all()`` and deleted by
    ``OutboxRelay`` after successful dispatch — there is no ``processed`` flag;
    deletion is the acknowledgement.
    """

    __allow_plain_fields__: ClassVar[bool] = True

    event_type: str
    event_data: str
    created_at: datetime


class IOutboxRepository(IRepository[UUID, Any, OutboxRecord], ABC):
    """Outbox repository — shares the UoW session so writes are atomic with domain changes."""

    def save_all(self, events: Iterable[DomainEvent]) -> None:
        """Serialize *events* and persist each as an ``OutboxRecord`` in the current session."""
        for event in events:
            event_type, event_data = serialize_event(event)
            self.save(OutboxRecord(
                event_type=event_type,
                event_data=event_data,
                created_at=datetime.now(UTC),
            ))

    @abstractmethod
    def get_pending(self) -> list[OutboxRecord]:
        """Return all pending outbox records ordered by creation time."""


class OutboxRelay:
    """Reads pending outbox records, dispatches via *bus*, then deletes each record."""

    def __init__(self, outbox_repo: IOutboxRepository, bus: EventBus) -> None:
        self._outbox_repo = outbox_repo
        self._bus = bus

    def run_once(self) -> None:
        """Process all pending outbox events in one pass."""
        for record in self._outbox_repo.get_pending():
            event = deserialize_event(record.event_type, record.event_data)
            self._bus.publish(event)
            self._outbox_repo.delete(record)

    def start(self, interval: float = 1.0) -> None:
        """Blocking polling loop — run in a background thread."""
        while True:
            self.run_once()
            time.sleep(interval)
