from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, final

from hike.domain_event import Event
from hike.events.interfaces import IEventHandler
from hike.events.interfaces.background_task import IBackgroundTasks


class IEventPublisher[TEvent: Event](IEventHandler[TEvent], ABC):
    """Other synonyms: event dispatcher/producer"""

    @abstractmethod
    def publish(self, events: Iterable[TEvent]) -> None:
        """Raises on failure."""

    @final
    def handle(self, event: TEvent) -> None:
        self.publish([event])


class IExternalEventPublisher[TEvent: Event](IEventPublisher[TEvent], IBackgroundTasks, ABC):
    """Other synonyms: event dispatcher/producer"""
