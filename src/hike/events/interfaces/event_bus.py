from __future__ import annotations

from abc import ABC

from hike.domain_event import Event
from hike.events.interfaces.publisher import IEventPublisher
from hike.events.interfaces.subscriber import IEventSubscriber


class IEventBus[TEvent: Event](IEventPublisher[TEvent], IEventSubscriber[TEvent], ABC):
    ...
