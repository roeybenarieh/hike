from __future__ import annotations

from typing import Any
from uuid import UUID

from hike.aggregate import Aggregate
from hike.persistence.outbox import IOutboxRepository, OutboxRecord
from hike.persistence.providers.in_memory.repository import InMemoryRepository


class InMemoryOutboxRepository(InMemoryRepository[UUID, OutboxRecord], IOutboxRepository):
    """In-memory outbox repository — suitable for tests and single-process apps."""

    def __init__(self) -> None:
        super().__init__()
        self._session: dict[Any, Aggregate[Any]] | None = {}

    def get_pending(self) -> list[OutboxRecord]:
        return [r for r in self.session.values() if isinstance(r, OutboxRecord)]
