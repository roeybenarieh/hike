from __future__ import annotations

from abc import ABC, abstractmethod

from hike.domain_event import Event


class IEventHandler[T: Event](ABC):

    @abstractmethod
    def handle(self, event: T) -> None:
        """might raise an error indicating that couldn't handle the event"""


class IReversibleEventHandler[T: Event](IEventHandler[T], ABC):

    @abstractmethod
    def compensate(self) -> None: ...
