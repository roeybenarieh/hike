from __future__ import annotations

import json
import logging
import uuid
from typing import Any, cast

from redis import Redis
from redis.exceptions import ResponseError

from hike.events.interfaces import IBrokerEventSubscriber

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


class RedisEventSubscriber(IBrokerEventSubscriber):
    """Receives domain events from Redis Streams using a consumer group.

    Call :meth:`subscribe` for each handler, then :meth:`start` to begin
    consuming (blocking).  Each event type is read from its own stream
    ``{stream_prefix}.{EventTypeName}`` via the configured consumer group.

    Multiple instances of the same subscriber class share the same consumer
    group by default (competing consumers): each message is delivered to
    exactly one instance.  If an instance crashes before acknowledging a
    message, the message sits in its Pending Entry List (PEL).  After
    *claim_idle_ms* milliseconds of inactivity, any surviving instance
    automatically reclaims and reprocesses those stranded messages via
    ``XAUTOCLAIM``.  Pass an explicit *group* name to override the default.

    **Ack/nack:** a message is acknowledged with ``XACK`` only after **all**
    registered handlers complete without raising.  If any handler raises, the
    message is left in the PEL and redelivered on the next poll iteration.

    Install with: ``pip install hike[redis]``
    """

    def __init__(
        self,
        client: Redis,  # type: ignore[type-arg]
        stream_prefix: str = "hike",
        group: str = "",
        consumer: str | None = None,
        claim_idle_ms: int = 30_000,
    ) -> None:
        super().__init__()
        self._client = client
        self._stream_prefix = stream_prefix
        # Default group name is derived from the concrete class so that all
        # instances of the same subscriber class share one group (competing
        # consumers) while different subscriber classes stay isolated.
        self._group = group or f"hike.consumer.{type(self).__name__}"
        self._consumer = consumer or uuid.uuid4().hex
        self._claim_idle_ms = claim_idle_ms
        self._running = False

    def _stream_key(self, event_type_name: str) -> str:
        return f"{self._stream_prefix}.{event_type_name}"

    def _dispatch(self, stream_key: str, messages: list[Any]) -> None:
        for entry in messages:
            msg_id: Any = entry[0]
            raw_fields: Any = entry[1]
            fields: dict[str, str] = {_decode(k): _decode(v) for k, v in raw_fields.items()}
            event_type_name = fields.get("event_type", "")
            event = self._event_classes[event_type_name].from_dict(json.loads(fields.get("data", "{}")))
            if event.id in self._seen_ids:
                self._client.xack(stream_key, self._group, msg_id)  # pyright: ignore[reportUnknownMemberType]
                _log.debug("Duplicate event %s skipped", event.id)
                continue
            try:
                for handler in self._handlers.get(event_type_name, []):
                    handler.handle(event)
                self._seen_ids.add(event.id)
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

        stream_keys = [self._stream_key(name) for name in self._handlers]
        self._running = True
        try:
            while self._running:
                # Reclaim messages from crashed peers that have been idle too long.
                for stream_key in stream_keys:
                    claimed: Any = self._client.xautoclaim(  # pyright: ignore[reportUnknownMemberType]
                        stream_key,
                        self._group,
                        self._consumer,
                        min_idle_time=self._claim_idle_ms,
                        start_id="0-0",
                        count=10,
                    )
                    # xautoclaim returns (next_start_id, entries, deleted_ids)
                    claimed_entries: list[Any] = claimed[1]
                    if claimed_entries:
                        self._dispatch(stream_key, claimed_entries)

                # Re-deliver any of OUR own previously unacknowledged messages.
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
