from __future__ import annotations

from typing import Any

from hike.aggregate import Aggregate
from hike.persistence.inbox import IInboxRepository, InboxRecord
from hike.persistence.providers.in_memory.repository import InMemoryRepository


class InMemoryInboxRepository(InMemoryRepository[str, InboxRecord], IInboxRepository):
    """In-memory inbox repository — suitable for tests and single-process apps."""

    def __init__(self) -> None:
        super().__init__()
        self._session: dict[Any, Aggregate[Any]] | None = {}
