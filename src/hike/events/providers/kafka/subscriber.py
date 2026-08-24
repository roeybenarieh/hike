from __future__ import annotations

import json
import logging
from confluent_kafka import Consumer, KafkaError, KafkaException
from confluent_kafka._types import HeadersType

from hike.events.interfaces import IBrokerEventSubscriber

_log = logging.getLogger(__name__)


def _header_value(headers: HeadersType, key: str) -> str:
    if isinstance(headers, dict):
        raw = headers.get(key, b"")
    else:
        raw = next((v for k, v in headers if k == key), b"")
    if isinstance(raw, bytes):
        return raw.decode()
    return raw or ""


class KafkaEventSubscriber(IBrokerEventSubscriber):
    """Consumes domain events from Kafka topics and dispatches them to registered handlers.

    Subscribe handlers before calling :meth:`start`.  Each unique event type
    is consumed from its own topic ``{topic_prefix}.{EventTypeName}``.

    The *consumer* must be configured with ``enable.auto.commit=false``; the
    subscriber commits offsets manually only after all handlers succeed.

    **Competing consumers / failover**: all instances that should share the
    work and provide failover must be created with the same ``group.id`` in
    their consumer config.  Kafka assigns each partition to exactly one
    consumer in the group; if an instance crashes before committing, Kafka
    rebalances and reassigns its partitions to surviving instances, which
    re-read the uncommitted messages.

    **Ack/nack:** if all handlers complete without raising, the message offset
    is committed synchronously.  If any handler raises, the offset is NOT
    committed — the message will be redelivered on the next consumer start or
    group rebalance.

    Install with: ``pip install hike[kafka]``
    """

    def __init__(self, consumer: Consumer, topic_prefix: str = "hike") -> None:
        super().__init__()
        self._consumer = consumer
        self._topic_prefix = topic_prefix
        self._running = False

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
                event = self._event_classes[event_type_name].from_dict(json.loads(raw_value.decode()))
                if event.id in self._seen_ids:
                    self._consumer.commit(message=msg, asynchronous=False)
                    _log.debug("Duplicate event %s skipped", event.id)
                    continue
                try:
                    for handler in self._handlers.get(event_type_name, []):
                        handler.handle(event)
                    self._seen_ids.add(event.id)
                    self._consumer.commit(message=msg, asynchronous=False)
                except Exception:
                    _log.exception("Handler failed for %s — offset not committed, message will be redelivered", event_type_name)
        finally:
            self._consumer.close()

    def close(self) -> None:
        self._running = False
