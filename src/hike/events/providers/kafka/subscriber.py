from __future__ import annotations

import json
import logging
from contextlib import suppress
from typing import NoReturn

from confluent_kafka import Consumer, KafkaError, KafkaException
from confluent_kafka._types import HeadersType

from hike.domain_event import DomainEvent
from hike.events.interfaces import IExternalEventSubscriber
from hike.events.interfaces.background_task import Task

_log = logging.getLogger(__name__)


def _header_value(headers: HeadersType, key: str) -> str:
    if isinstance(headers, dict):
        raw = headers.get(key, b"")
    else:
        raw = next((v for k, v in headers if k == key), b"")
    if isinstance(raw, bytes):
        return raw.decode()
    return raw or ""


class KafkaEventSubscriber(IExternalEventSubscriber[DomainEvent]):
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

    def start(self) -> NoReturn:
        topics = [f"{self._topic_prefix}.{name}" for name in self._handlers]
        self._consumer.subscribe(topics)
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
            event = self._deserialize(event_type_name, json.loads(raw_value.decode()))
            try:
                self._dispatch_to_handlers(event_type_name, event)
                self._consumer.commit(message=msg, asynchronous=False)
            except Exception:
                _log.exception("Handler failed for %s — offset not committed, message will be redelivered",
                               event_type_name)
        with suppress(Exception):
            self._consumer.close()
        raise RuntimeError("subscriber stopped")

    def tasks(self) -> list[Task]:
        return [self.start]

    def cleanup(self) -> None:
        super().cleanup()
