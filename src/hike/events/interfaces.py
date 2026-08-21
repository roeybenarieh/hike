from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, List, Iterator, final

from hike import DomainEvent


class _StartCloseContext(ABC):
    @abstractmethod
    def close(self) -> None:
        """Must be none blocking"""

    @abstractmethod
    def start(self) -> None:
        """Must be none blocking"""

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

        if exc_type is not None:
            raise exc_val


EventHandler = Callable[[DomainEvent], None]


class EventConsumer(_StartCloseContext):
    """Other synonyms: event handler"""

    def __init__(self, callbacks: List[EventHandler] | None = None):
        if callbacks is None:
            callbacks = []

        self._callbacks: List[EventHandler] = callbacks

    @final
    def register(self, callback: EventHandler):
        self._callbacks.append(callback)

    @abstractmethod
    def consume(self) -> Iterator[DomainEvent]:
        raise NotImplementedError("consume() is not implemented")


class EventProducer(_StartCloseContext):
    """Other synonyms: event dispatcher"""

    @abstractmethod
    def produce(self, event: DomainEvent) -> None:
        raise NotImplementedError("produce() is not implemented")
