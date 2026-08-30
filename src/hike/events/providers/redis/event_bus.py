from __future__ import annotations

from typing import Iterable

from redis import Redis

from hike.events.integration_event import IntegrationEvent
from hike.events.interfaces.event_bus import IExternalEventBus
from hike.events.providers.redis.publisher import RedisEventPublisher
from hike.events.providers.redis.subscriber import RedisEventSubscriber


class RedisEventBus(RedisEventSubscriber, IExternalEventBus[IntegrationEvent]):
    """Combined publisher + subscriber for Redis Streams.

    Uses a single Redis client for both publishing (XADD) and consuming
    (XREADGROUP/XACK).  The *group*, *consumer*, and *claim_idle_ms* parameters
    are forwarded to :class:`RedisEventSubscriber`.

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
        super().__init__(client, stream_prefix, group, consumer, claim_idle_ms)
        self._publisher = RedisEventPublisher(client, stream_prefix)

    def publish(self, events: Iterable[IntegrationEvent]) -> None:
        self._publisher.publish(events)
