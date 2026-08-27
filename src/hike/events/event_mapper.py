from typing import Iterable

from hike.domain_event import Event
from hike.events.integration_event import IntegrationEvent
from hike.events.interfaces import IEventPublisher


# TODO: finish this
class EventMapper:
    def __init__(self, raise_on_mismatch: bool = True):
        self._map: dict[Event, IntegrationEvent] = {}
        self.raise_on_mismatch = raise_on_mismatch

    def map(self, event: Event) -> IntegrationEvent:
        ...


class MappedEventPublisher[TEvent: Event](IEventPublisher[TEvent]):
    """Decorates a publisher with mapping capability"""

    def __init__(self, publisher: IEventPublisher[IntegrationEvent], mapper: EventMapper) -> None:
        self.publisher = publisher
        self.mapper = mapper

    def publish(self, events: Iterable[TEvent]) -> None:
        integration_events = [self.mapper.map(event) for event in events]
        self.publisher.publish(integration_events)
