"""RabbitMQ provider for hike event pub/sub.

Requires the ``rabbitmq`` extra::

    pip install hike[rabbitmq]
"""

from __future__ import annotations

try:
    import pika  # noqa: F401  # pyright: ignore[reportUnusedImport]
except ImportError as _exc:
    raise ImportError(
        "The rabbitmq extra is required for this provider. "
        "Install it with: pip install hike[rabbitmq]"
    ) from _exc

from hike.events.providers.rabbitmq.publisher import RabbitMQEventPublisher
from hike.events.providers.rabbitmq.subscriber import RabbitMQEventSubscriber

__all__ = [
    "RabbitMQEventPublisher",
    "RabbitMQEventSubscriber",
]
