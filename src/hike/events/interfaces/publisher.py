from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable, final

from hike.domain_event import DomainEvent
from hike.events.interfaces import IEventHandler
from hike.events.interfaces.background_task import IBackgroundTasks


class IEventPublisher[TDomainEvent: DomainEvent](IEventHandler[Any], ABC):  # type: ignore[type-arg]
    """Other synonyms: event dispatcher/producer"""

    @abstractmethod
    def publish(self, events: Iterable[TDomainEvent]) -> None:
        """Raises on failure."""

    @final
    def handle(self, event: Iterable[TDomainEvent]) -> None:  # type: ignore[override]
        self.publish(event)


class IExternalEventPublisher[T: DomainEvent](IEventPublisher[T], IBackgroundTasks, ABC):
    """Other synonyms: event dispatcher/producer"""
