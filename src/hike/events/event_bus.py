from typing import Iterable

from hike.domain_event import DomainEvent
from hike.events.interfaces import IEventBus, IEventHandler, IReversibleEventHandler



class EventBus(IEventBus):
    """Synchronous in-memory bus — wires up producers to consumers."""

    def __init__(self) -> None:
        self._reversible: dict[type[DomainEvent], list[IReversibleEventHandler[DomainEvent]]] = {}
        self._regular: dict[type[DomainEvent], list[IEventHandler[DomainEvent]]] = {}

    def _subscribe(self, event_type: str, event_class: type[DomainEvent], event_handler: IEventHandler[DomainEvent]) -> None:
        if isinstance(event_handler, IReversibleEventHandler):
            self._reversible.setdefault(event_class, []).append(event_handler)  # type: ignore[arg-type]
        else:
            self._regular.setdefault(event_class, []).append(event_handler)  # type: ignore[arg-type]

    def publish(self, events: Iterable[DomainEvent]) -> None:
        """must first publish to reversible event handlers, then to regular handlers, if error occurs reverse them."""
        for event in events:
            reversible = self._reversible.get(type(event), [])
            regular = self._regular.get(type(event), [])

            succeeded: list[IReversibleEventHandler[DomainEvent]] = []
            try:
                for h in reversible:
                    h.handle(event)
                    succeeded.append(h)
                for h in regular:
                    h.handle(event)
            except Exception:
                for h in reversed(succeeded):
                    h.compensate()
                raise
