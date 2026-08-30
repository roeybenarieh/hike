from __future__ import annotations

from abc import ABC

from hike.domain_event import Event
from hike.events.integration_event import IntegrationEvent
from hike.events.interfaces.background_task import IBackgroundTasks
from hike.events.interfaces.publisher import IEventPublisher
from hike.events.interfaces.subscriber import IEventSubscriber


class IEventBus[TEvent: Event](IEventPublisher[TEvent], IEventSubscriber[TEvent], ABC):
    ...


class IExternalEventBus[TEvent: IntegrationEvent](IEventBus[TEvent], IBackgroundTasks, ABC):
    ...
