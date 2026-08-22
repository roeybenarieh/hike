from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Iterable, Self

from hike.domain_event import DomainEvent


class IEventHandler[TDomainEvent: DomainEvent](ABC):

    @abstractmethod
    def handle(self, event: TDomainEvent) -> None: ...

    def __call__(self, event: TDomainEvent) -> None:
        self.handle(event)


class IReversibleEventHandler[TDomainEvent: DomainEvent](IEventHandler[TDomainEvent], ABC):

    @abstractmethod
    def compensate(self) -> None: ...


class IEventSubscriber[TDomainEvent: DomainEvent](ABC):

    @abstractmethod
    def subscribe(self, event_handler: IEventHandler[TDomainEvent]) -> None:
        """multiple calls to this method is supported"""


class IBlockingEventSubscriber(ABC):

    @abstractmethod
    def subscribe[TEvent: DomainEvent](self, event_handler: IEventHandler[TEvent]) -> None:
        """multiple calls to this method is supported"""

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def start(self) -> None:
        """blocking method"""

    def __enter__(self) -> Self:
        return self

    def __exit__(
            self,
            _exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            _exc_tb: TracebackType | None,
    ) -> None:
        self.close()

        if exc_val is not None:
            raise exc_val


class IEventPublisher[TDomainEvent: DomainEvent](ABC):
    """Other synonyms: event dispatcher/producer"""

    @abstractmethod
    def publish(self, events: Iterable[TDomainEvent]) -> None: ...


class IEventBus(IEventPublisher[DomainEvent], IEventSubscriber[DomainEvent], ABC):
    ...
