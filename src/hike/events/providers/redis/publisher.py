from __future__ import annotations

from typing import Iterable

from redis import Redis

import json

from hike.domain_event import DomainEvent
from hike.events.interfaces import IEventPublisher


class RedisEventPublisher(IEventPublisher[DomainEvent]):
    """Publishes domain events to Redis Streams.

    Each event type is appended to its own stream:
    ``{stream_prefix}.{EventTypeName}``.

    **Delivery guarantee:** :meth:`publish` raises if the ``XADD`` command
    fails (connection error, server unavailable, out-of-memory policy, etc.).
    The message is durably stored in the stream; delivery to consumers depends
    on whether a consumer group exists and whether consumers acknowledge the
    message via :class:`RedisEventSubscriber`.

    Install with: ``pip install hike[redis]``
    """

    def __init__(self, client: Redis, stream_prefix: str = "hike") -> None:  # type: ignore[type-arg]
        self._client = client
        self._stream_prefix = stream_prefix

    def publish(self, events: Iterable[DomainEvent]) -> None:
        for event in events:
            event_type = event.event_name
            json_data = json.dumps(event.to_dict(), default=str)
            stream_key = f"{self._stream_prefix}.{event_type}"
            self._client.xadd(  # pyright: ignore[reportUnknownMemberType]
                stream_key,
                {"event_type": event_type, "data": json_data},
            )
