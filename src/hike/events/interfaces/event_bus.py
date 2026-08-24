from __future__ import annotations

from abc import ABC

from hike.domain_event import DomainEvent
from hike.events.interfaces.publisher import IEventPublisher
from hike.events.interfaces.subscriber import IEventSubscriber


class IEventBus(IEventPublisher[DomainEvent], IEventSubscriber[DomainEvent], ABC):
    ...
