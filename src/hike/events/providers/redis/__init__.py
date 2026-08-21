"""Redis Pub/Sub provider for hike event pub/sub.

Requires the ``redis`` extra::

    pip install hike[redis]

.. note::
    Redis Pub/Sub is a **fire-and-forget** transport: messages are not persisted
    and subscribers that are offline when an event is published will miss it.
    ``AcknowledgementResult.NACK`` returned by handlers is noted but cannot
    trigger redelivery — use RabbitMQ or Kafka if reliable delivery is required.
"""

from __future__ import annotations

try:
    import redis  # noqa: F401  # pyright: ignore[reportUnusedImport, reportMissingImports]
except ImportError as _exc:
    raise ImportError(
        "The redis extra is required for this provider. "
        "Install it with: pip install hike[redis]"
    ) from _exc

from hike.events.providers.redis.publisher import RedisEventPublisher
from hike.events.providers.redis.subscriber import RedisEventSubscriber

__all__ = [
    "RedisEventPublisher",
    "RedisEventSubscriber",
]
