from __future__ import annotations

from typing import Iterable

import pika
from pika.adapters.blocking_connection import BlockingChannel

import json

from hike.domain_event import DomainEvent
from hike.events.interfaces import IEventPublisher


class RabbitMQEventPublisher(IEventPublisher[DomainEvent]):
    """Publishes domain events to a RabbitMQ topic exchange.

    Each event is routed via its class name as the routing key, so consumers
    can bind queues to specific event types.  Publisher confirms are enabled
    on the channel: :meth:`publish` raises :exc:`pika.exceptions.NackError` if
    the broker nacks a message, or :exc:`pika.exceptions.UnroutableError` if a
    message cannot be routed (requires ``mandatory=True`` at the pika level,
    which this publisher does not set — unroutable messages are silently dropped
    by the broker unless a dead-letter exchange is configured).

    Install with: ``pip install hike[rabbitmq]``
    """

    def __init__(
        self,
        channel: BlockingChannel,
        exchange: str = "hike.events",
    ) -> None:
        self._channel = channel
        self._exchange = exchange
        self._channel.exchange_declare(
            exchange=exchange, exchange_type="topic", durable=True
        )
        self._channel.confirm_delivery()

    def publish(self, events: Iterable[DomainEvent]) -> None:
        for event in events:
            event_type = event.event_type()
            json_data = json.dumps(event.to_dict(), default=str)
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
