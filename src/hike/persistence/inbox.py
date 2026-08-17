from __future__ import annotations

from abc import ABC
from datetime import UTC, datetime
from typing import Any, ClassVar

from hike.aggregate import Aggregate
from hike.domain_event import DomainEvent, EventBus, serialize_event
from hike.entity import EntityID, Field
from hike.persistence.repository import AggregateDoesNotExistError, IRepository


class InboxRecord(Aggregate[str]):
    """Infrastructure aggregate for exactly-once event processing.

    The *event_id* (from the message broker) IS the ``id`` — prevents duplicates.
    ``processed`` flips to ``True`` after the event is dispatched to the local bus.
    """

    __allow_plain_fields__: ClassVar[bool] = True

    id: Field[EntityID[str]]  # required — no auto-generated default
    event_type: str
    event_data: str
    received_at: datetime
    processed: bool = False


# TODO: Allow for none str inbox/outbox record id
# TODO: make event_data not a JSON string
class IInboxRepository(IRepository[str, InboxRecord, Any], ABC):
    """Inbox repository with concrete idempotency helpers."""

    def is_processed(self, event_id: str) -> bool:
        try:
            return self.get_one(EntityID(event_id)).processed
        except AggregateDoesNotExistError:
            return False

    def record(self, event_id: str, event: DomainEvent) -> None:
        event_type, event_data = serialize_event(event)
        self.save(InboxRecord(
            id=EntityID(event_id),
            event_type=event_type,
            event_data=event_data,
            received_at=datetime.now(UTC),
        ))

    def mark_processed(self, event_id: str) -> None:
        inbox_record = self.get_one(EntityID(event_id))
        inbox_record.processed = True
        self.upsert(inbox_record)


class InboxProcessor:
    """Ensures exactly-once processing of incoming external events."""

    def __init__(self, inbox_repo: IInboxRepository, bus: EventBus) -> None:
        self._inbox_repo = inbox_repo
        self._bus = bus

    def process(self, event_id: str, event: DomainEvent) -> None:
        if self._inbox_repo.is_processed(event_id):
            return
        self._inbox_repo.record(event_id, event)
        self._bus.publish(event)
        self._inbox_repo.mark_processed(event_id)
