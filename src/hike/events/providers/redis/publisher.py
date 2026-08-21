from __future__ import annotations

import json
from typing import Iterable

from redis import Redis

from hike.domain_event import DomainEvent, serialize_event
from hike.events.interfaces import IEventPublisher


class RedisEventPublisher(IEventPublisher[DomainEvent]):
    """Publishes domain events via Redis Pub/Sub.

    Each event type is published to a channel named
    ``{channel_prefix}.{EventTypeName}``.

    Install with: ``pip install hike[redis]``
    """

    def __init__(self, client: Redis, channel_prefix: str = "hike") -> None:
        self._client = client
        self._channel_prefix = channel_prefix

    def publish(self, events: Iterable[DomainEvent]) -> None:
        for event in events:
            event_type, json_data = serialize_event(event)
            channel = f"{self._channel_prefix}.{event_type}"
            payload = json.dumps({"event_type": event_type, "data": json_data})
            self._client.publish(channel, payload)  # pyright: ignore[reportUnknownMemberType]
