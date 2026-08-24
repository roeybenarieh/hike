from __future__ import annotations

from abc import ABC, abstractmethod

from hike.domain_event import DomainEvent


class IEventHandler[TDomainEvent: DomainEvent](ABC):

    @abstractmethod
    def handle(self, event: TDomainEvent) -> None:
        """might raise an error indicating that couldn't handle the event"""

    def __call__(self, event: TDomainEvent) -> None:
        self.handle(event)


class IReversibleEventHandler[TDomainEvent: DomainEvent](IEventHandler[TDomainEvent], ABC):

    @abstractmethod
    def compensate(self) -> None: ...
