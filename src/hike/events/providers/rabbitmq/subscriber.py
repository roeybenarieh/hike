from __future__ import annotations

import logging

import pika.spec
from pika.adapters.blocking_connection import BlockingChannel

from hike.domain_event import DomainEvent, deserialize_event
from hike.events.interfaces import IBlockingEventSubscriber, IEventHandler
from hike.events.utils import event_type_for

_log = logging.getLogger(__name__)


class RabbitMQEventSubscriber(IBlockingEventSubscriber):
    """Consumes domain events from RabbitMQ and dispatches them to registered handlers.

    Binds a durable queue to the configured exchange using each event type's class
    name as the routing key.  Call :meth:`subscribe` for each handler, then
    :meth:`start` to begin consuming (blocking).

    **Ack/nack:** if all handlers complete without raising, the message is
    ``basic_ack``-ed and the consumer moves on.  If any handler raises, the
    message is ``basic_nack``-ed with ``requeue=True`` so it stays in the broker
    for redelivery; the exception is logged and the consumer continues.

    Install with: ``pip install hike[rabbitmq]``
    """

    def __init__(
        self,
        channel: BlockingChannel,
        exchange: str = "hike.events",
        queue: str = "hike.consumer",
    ) -> None:
        self._channel = channel
        self._exchange = exchange
        self._queue = queue
        self._handlers: dict[str, list[IEventHandler[DomainEvent]]] = {}
        self._channel.exchange_declare(
            exchange=exchange, exchange_type="topic", durable=True
        )
        self._channel.queue_declare(queue=queue, durable=True)

    def subscribe[TEvent: DomainEvent](self, event_handler: IEventHandler[TEvent]) -> None:
        event_type = event_type_for(event_handler)
        event_type_name = event_type.__name__
        self._handlers.setdefault(event_type_name, []).append(event_handler)  # type: ignore[arg-type]
        self._channel.queue_bind(
            exchange=self._exchange,
            queue=self._queue,
            routing_key=event_type_name,
        )

    def start(self) -> None:
        def _on_message(
            ch: BlockingChannel,
            method: pika.spec.Basic.Deliver,
            properties: pika.spec.BasicProperties,
            body: bytes,
        ) -> None:
            event_type_name = str((properties.headers or {}).get("event_type", ""))
            event = deserialize_event(event_type_name, body.decode())
            try:
                for handler in self._handlers.get(event_type_name, []):
                    handler.handle(event)
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except Exception:
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                _log.exception("Handler failed for %s — message nacked and requeued", event_type_name)

        self._channel.basic_qos(prefetch_count=1)
        self._channel.basic_consume(queue=self._queue, on_message_callback=_on_message)
        self._channel.start_consuming()

    def close(self) -> None:
        self._channel.stop_consuming()
