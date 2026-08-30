from __future__ import annotations

from typing import Iterable

from pika.adapters.blocking_connection import BlockingChannel

from hike.events.integration_event import IntegrationEvent
from hike.events.interfaces.event_bus import IExternalEventBus
from hike.events.providers.rabbitmq.publisher import RabbitMQEventPublisher
from hike.events.providers.rabbitmq.subscriber import RabbitMQEventSubscriber


class RabbitMQEventBus(RabbitMQEventSubscriber, IExternalEventBus[IntegrationEvent]):
    """Combined publisher + subscriber for RabbitMQ.

    Uses two separate channels: one for publishing (with delivery confirms) and
    one for consuming (with manual ack/nack).  Both channels must connect to the
    same broker; pass the same *exchange* and *queue* to align with existing
    subscribers.

    Install with: ``pip install hike[rabbitmq]``
    """

    def __init__(
            self,
            pub_channel: BlockingChannel,
            sub_channel: BlockingChannel,
            exchange: str = "hike.events",
            queue: str = "",
    ) -> None:
        super().__init__(sub_channel, exchange, queue)
        self._publisher = RabbitMQEventPublisher(pub_channel, exchange)

    def publish(self, events: Iterable[IntegrationEvent]) -> None:
        self._publisher.publish(events)
