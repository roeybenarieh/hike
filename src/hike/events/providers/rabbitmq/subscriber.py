from __future__ import annotations

import json
import logging
from typing import NoReturn

import pika.spec
from pika.adapters.blocking_connection import BlockingChannel
from pika.exceptions import StreamLostError

from hike.domain_event import DomainEvent
from hike.events.interfaces import IExternalEventSubscriber
from hike.events.interfaces.background_task import Task

_log = logging.getLogger(__name__)


class RabbitMQEventSubscriber(IExternalEventSubscriber[DomainEvent]):
    """Consumes domain events from RabbitMQ and dispatches them to registered handlers.

    Binds a shared durable queue to the configured exchange using each event
    type's class name as the routing key.  Call :meth:`subscribe` for each
    handler, then :meth:`start` to begin consuming (blocking).

    Multiple instances of the same subscriber class share the same queue by
    default (competing consumers).  If one instance crashes while processing a
    message, RabbitMQ redelivers the unacked message to one of the remaining
    instances.  Pass an explicit *queue* name to override the default.

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
            queue: str = "",
    ) -> None:
        super().__init__()
        self._channel = channel
        self._exchange = exchange
        # Default queue name is derived from the concrete class so that all
        # instances of the same subscriber class share one queue (competing
        # consumers) while different subscriber classes stay isolated.
        self._queue = queue or f"hike.consumer.{type(self).__name__}"
        self._channel.exchange_declare(
            exchange=exchange, exchange_type="topic", durable=True
        )
        self._channel.queue_declare(queue=self._queue, durable=True)

    def _on_subscribe(self, event_type_name: str, event_type: type[DomainEvent]) -> None:
        self._channel.queue_bind(
            exchange=self._exchange,
            queue=self._queue,
            routing_key=event_type_name,
        )

    def start(self) -> NoReturn:
        def _on_message(
                ch: BlockingChannel,
                method: pika.spec.Basic.Deliver,
                properties: pika.spec.BasicProperties,
                body: bytes,
        ) -> None:
            event_type_name = str((properties.headers or {}).get("event_type", ""))
            event = self._deserialize(event_type_name, json.loads(body.decode()))
            try:
                self._dispatch_to_handlers(event_type_name, event)
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except Exception:
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                _log.exception("Handler failed for %s — message nacked and requeued", event_type_name)

        self._channel.basic_qos(prefetch_count=1)
        self._channel.basic_consume(queue=self._queue, on_message_callback=_on_message)
        try:
            self._channel.start_consuming()
        except StreamLostError as exc:
            raise RuntimeError("RabbitMQ stream lost") from exc
        raise RuntimeError("start_consuming() returned unexpectedly")

    def tasks(self) -> list[Task]:
        return [self.start]

    def cleanup(self) -> None:
        super().cleanup()
        try:
            self._channel.stop_consuming()
        except Exception:
            pass
