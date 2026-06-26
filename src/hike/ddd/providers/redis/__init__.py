"""Redis provider for hike DDD building blocks.

Requires the ``redis`` extra::

    pip install hike[redis]
"""

from __future__ import annotations

try:
    import redis  # noqa: F401  # pyright: ignore[reportUnusedImport]
except ImportError as _exc:
    raise ImportError(
        "The redis extra is required for this provider. "
        "Install it with: pip install hike[redis]"
    ) from _exc

from hike.ddd.providers.redis.db_context import RedisDBContext
from hike.ddd.providers.redis.repository import RedisRepository

__all__ = [
    "RedisDBContext",
    "RedisRepository",
]
