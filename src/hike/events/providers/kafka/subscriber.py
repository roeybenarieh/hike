from __future__ import annotations

import logging

from confluent_kafka import Consumer, KafkaError, KafkaException
from confluent_kafka._types import HeadersType

from hike.domain_event import DomainEvent, deserialize_event
from hike.events.interfaces import IBlockingEventSubscriber, IEventHandler
from hike.events.utils import event_type_for

_log = logging.getLogger(__name__)


def _header_value(headers: HeadersType, key: str) -> str:
    if isinstance(headers, dict):
        raw = headers.get(key, b"")
    else:
        raw = next((v for k, v in headers if k == key), b"")
    if isinstance(raw, bytes):
        return raw.decode()
    return raw or ""


class KafkaEventSubscriber(IBlockingEventSubscriber):
    """Consumes domain events from Kafka topics and dispatches them to registered handlers.

    Subscribe handlers before calling :meth:`start`.  Each unique event type
    is consumed from its own topic ``{topic_prefix}.{EventTypeName}``.

    **Ack/nack:** if all handlers complete without raising, the message offset is
    committed synchronously.  If any handler raises, the offset is NOT committed —
    the message will be redelivered on the next consumer start or group rebalance
    (requires ``enable.auto.commit=false`` in the consumer config).  The exception
    is logged and the consumer continues processing subsequent messages.

    Install with: ``pip install hike[kafka]``
    """

    def __init__(self, consumer: Consumer, topic_prefix: str = "hike") -> None:
        self._consumer = consumer
        self._topic_prefix = topic_prefix
        self._handlers: dict[str, list[IEventHandler[DomainEvent]]] = {}
        self._running = False

    def subscribe[TEvent: DomainEvent](self, event_handler: IEventHandler[TEvent]) -> None:
        event_type = event_type_for(event_handler)
        self._handlers.setdefault(event_type.__name__, []).append(event_handler)  # type: ignore[arg-type]

    def start(self) -> None:
        topics = [f"{self._topic_prefix}.{name}" for name in self._handlers]
        self._consumer.subscribe(topics)
        self._running = True
        try:
            while self._running:
                msg = self._consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                error = msg.error()
                if error:
                    if error.code() == KafkaError.UNKNOWN_TOPIC_OR_PART:
                        _log.debug("Topic not yet available, retrying: %s", error)
                        continue
                    raise KafkaException(error)
                raw_value = msg.value()
                if raw_value is None:
                    continue
                raw_headers = msg.headers()
                event_type_name = _header_value(raw_headers, "event_type") if raw_headers is not None else ""
                event = deserialize_event(event_type_name, raw_value.decode())
                try:
                    for handler in self._handlers.get(event_type_name, []):
                        handler.handle(event)
                    self._consumer.commit(message=msg, asynchronous=False)
                except Exception:
                    _log.exception("Handler failed for %s — offset not committed, message will be redelivered", event_type_name)
        finally:
            self._consumer.close()

    def close(self) -> None:
        self._running = False
