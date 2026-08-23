"""Redis Streams provider for hike event pub/sub.

Requires the ``redis`` extra::

    pip install hike[redis]

Messages are appended to Redis Streams (``XADD``) and consumed via consumer
groups (``XREADGROUP``/``XACK``).  A message is acknowledged only after all
handlers succeed; failed messages remain in the consumer's Pending Entry List
and are redelivered on the next poll iteration.
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
