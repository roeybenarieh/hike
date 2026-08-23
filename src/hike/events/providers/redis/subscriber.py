from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from redis import Redis
from redis.exceptions import ResponseError

from hike.domain_event import DomainEvent, deserialize_event
from hike.events.interfaces import IBlockingEventSubscriber, IEventHandler
from hike.events.utils import event_type_for

_log = logging.getLogger(__name__)

# Shape returned by xreadgroup: [(stream_key, [(msg_id, {field: value})])]
_XReadResponse = list[tuple[Any, list[Any]]]


def _decode(val: Any, default: str = "") -> str:
    """Return *val* as a plain str regardless of whether Redis returned bytes or str."""
    if val is None:
        return default
    if isinstance(val, (bytes, bytearray)):
        return val.decode()
    return str(val)


class RedisEventSubscriber(IBlockingEventSubscriber):
    """Receives domain events from Redis Streams using a consumer group.

    Call :meth:`subscribe` for each handler, then :meth:`start` to begin
    consuming (blocking).  Each event type is read from its own stream
    ``{stream_prefix}.{EventTypeName}`` via the configured consumer group.

    **Ack/nack:** a message is acknowledged with ``XACK`` only after **all**
    registered handlers complete without raising.  If any handler raises, the
    message is left in the consumer's Pending Entry List (PEL) and redelivered
    on the next iteration of the poll loop.  This matches the at-least-once
    delivery guarantee provided by :class:`KafkaEventSubscriber` and
    :class:`RabbitMQEventSubscriber`.

    **Consumer groups:** the *group* parameter names the consumer group.
    Multiple subscriber instances with the same *group* share the message load
    (each message is delivered to exactly one consumer in the group).  Use
    different *group* names for independent subscribers that each need a full
    copy of every message.

    The consumer group is created with ``MKSTREAM`` and ``id="$"`` on first
    :meth:`start`, so only messages published *after* the subscriber starts are
    delivered.  If the group already exists (e.g., on restart), the subscriber
    resumes from the last acknowledged position and first re-delivers any
    pending (unacknowledged) messages.

    Install with: ``pip install hike[redis]``
    """

    def __init__(
        self,
        client: Redis,  # type: ignore[type-arg]
        stream_prefix: str = "hike",
        group: str = "hike.consumers",
        consumer: str | None = None,
    ) -> None:
        self._client = client
        self._stream_prefix = stream_prefix
        self._group = group
        self._consumer = consumer or uuid.uuid4().hex
        self._handlers: dict[str, list[IEventHandler[DomainEvent]]] = {}
        self._running = False

    def subscribe[TEvent: DomainEvent](self, event_handler: IEventHandler[TEvent]) -> None:
        event_type = event_type_for(event_handler)
        self._handlers.setdefault(event_type.__name__, []).append(event_handler)  # type: ignore[arg-type]

    def _stream_key(self, event_type_name: str) -> str:
        return f"{self._stream_prefix}.{event_type_name}"

    def _dispatch(self, stream_key: str, messages: list[Any]) -> None:
        for entry in messages:
            msg_id: Any = entry[0]
            raw_fields: Any = entry[1]
            fields: dict[str, str] = {_decode(k): _decode(v) for k, v in raw_fields.items()}
            event_type_name = fields.get("event_type", "")
            event = deserialize_event(event_type_name, fields.get("data", "{}"))
            try:
                for handler in self._handlers.get(event_type_name, []):
                    handler.handle(event)
                self._client.xack(stream_key, self._group, msg_id)  # pyright: ignore[reportUnknownMemberType]
            except Exception:
                _log.exception(
                    "Handler failed for %s — message not acknowledged, will be redelivered",
                    event_type_name,
                )

    def start(self) -> None:
        for event_type_name in self._handlers:
            stream_key = self._stream_key(event_type_name)
            try:
                self._client.xgroup_create(  # pyright: ignore[reportUnknownMemberType]
                    stream_key, self._group, id="$", mkstream=True
                )
            except ResponseError:
                pass  # BUSYGROUP: group already exists, resume from last committed position

        stream_keys = {self._stream_key(name) for name in self._handlers}
        self._running = True
        try:
            while self._running:
                # Re-deliver any previously unacknowledged messages before reading new ones.
                pending = cast(
                    _XReadResponse,
                    self._client.xreadgroup(  # pyright: ignore[reportUnknownMemberType]
                        self._group,
                        self._consumer,
                        {k: "0" for k in stream_keys},
                        count=10,
                    ) or [],
                )
                for raw_key, messages in pending:
                    if messages:
                        self._dispatch(_decode(raw_key), messages)

                # Block briefly waiting for new messages from the broker.
                new = cast(
                    _XReadResponse,
                    self._client.xreadgroup(  # pyright: ignore[reportUnknownMemberType]
                        self._group,
                        self._consumer,
                        {k: ">" for k in stream_keys},
                        count=10,
                        block=1000,
                    ) or [],
                )
                for raw_key, messages in new:
                    if messages:
                        self._dispatch(_decode(raw_key), messages)
        finally:
            pass

    def close(self) -> None:
        self._running = False
