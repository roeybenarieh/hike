from __future__ import annotations

from typing import Iterable, Union

import pika
import pika.channel
from pika.adapters.blocking_connection import BlockingChannel

from hike.domain_event import DomainEvent, serialize_event
from hike.events.interfaces import IEventPublisher

# pika's blocking and async channel implementations share the same interface
# but don't share a common base class; accept either.
_Channel = Union[pika.channel.Channel, BlockingChannel]


class RabbitMQEventPublisher(IEventPublisher[DomainEvent]):
    """Publishes domain events to a RabbitMQ topic exchange.

    Each event is routed via its class name as the routing key, so consumers
    can bind queues to specific event types.

    Install with: ``pip install hike[rabbitmq]``
    """

    def __init__(
        self,
        channel: _Channel,
        exchange: str = "hike.events",
    ) -> None:
        self._channel = channel
        self._exchange = exchange
        self._channel.exchange_declare(
            exchange=exchange, exchange_type="topic", durable=True
        )

    def publish(self, events: Iterable[DomainEvent]) -> None:
        for event in events:
            event_type, json_data = serialize_event(event)
            self._channel.basic_publish(
                exchange=self._exchange,
                routing_key=event_type,
                body=json_data.encode(),
                properties=pika.BasicProperties(
                    content_type="application/json",
                    headers={"event_type": event_type},
                    delivery_mode=pika.DeliveryMode.Persistent,
                ),
            )
