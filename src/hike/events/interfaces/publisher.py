from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from hike.domain_event import DomainEvent
from hike.events.interfaces.background_task import BackgroundTask


class IEventPublisher[TDomainEvent: DomainEvent](ABC):
    """Other synonyms: event dispatcher/producer"""

    @abstractmethod
    def publish(self, events: Iterable[TDomainEvent]) -> None: ...


class IBrokerEventPublisher[TDomainEvent: DomainEvent](IEventPublisher[TDomainEvent], BackgroundTask, ABC): ...
