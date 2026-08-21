from __future__ import annotations

import json
import logging
from typing import Any

from redis import Redis

from hike.domain_event import DomainEvent, deserialize_event
from hike.events.interfaces import IBlockingEventSubscriber, IEventHandler
from hike.events.utils import event_type_for

_log = logging.getLogger(__name__)


class RedisEventSubscriber(IBlockingEventSubscriber):
    """Receives domain events via Redis Pub/Sub.

    Call :meth:`subscribe` for each handler, then :meth:`start` to begin
    listening (blocking).  Each unique event type is subscribed on its own
    channel ``{channel_prefix}.{EventTypeName}``.

    **Ack/nack:** Redis Pub/Sub is fire-and-forget — there is no broker-level
    ack/nack mechanism and no message persistence.  Handler exceptions are logged
    but cannot trigger redelivery.  Use RabbitMQ or Kafka if reliable delivery
    is required.

    Install with: ``pip install hike[redis]``
    """

    def __init__(self, client: Redis, channel_prefix: str = "hike") -> None:
        self._client = client
        self._channel_prefix = channel_prefix
        self._handlers: dict[str, list[IEventHandler[DomainEvent]]] = {}
        # PubSub stubs use *args/**kwargs — annotate as Any to avoid cascading Unknown errors
        self._pubsub: Any = client.pubsub()  # pyright: ignore[reportUnknownMemberType]
        self._running = False

    def subscribe[TEvent: DomainEvent](self, event_handler: IEventHandler[TEvent]) -> None:
        event_type = event_type_for(event_handler)
        event_type_name = event_type.__name__
        channel = f"{self._channel_prefix}.{event_type_name}"
        self._handlers.setdefault(event_type_name, []).append(event_handler)  # type: ignore[arg-type]
        self._pubsub.subscribe(channel)

    def start(self) -> None:
        self._running = True
        try:
            while self._running:
                message = self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message is None:
                    continue
                raw_data = message.get("data")
                if not isinstance(raw_data, bytes):
                    continue
                payload: dict[str, str] = json.loads(raw_data.decode())
                event_type_name = payload.get("event_type", "")
                event = deserialize_event(event_type_name, payload.get("data", "{}"))
                for handler in self._handlers.get(event_type_name, []):
                    try:
                        handler.handle(event)
                    except Exception:
                        _log.exception(
                            "%s failed handling %s — Redis Pub/Sub cannot redeliver",
                            type(handler).__name__,
                            event_type_name,
                        )
        finally:
            self._pubsub.close()

    def close(self) -> None:
        self._running = False
