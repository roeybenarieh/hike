"""Kafka provider for hike event pub/sub.

Requires the ``kafka`` extra::

    pip install hike[kafka]
"""

from __future__ import annotations

try:
    import confluent_kafka  # noqa: F401  # pyright: ignore[reportUnusedImport, reportMissingImports]
except ImportError as _exc:
    raise ImportError(
        "The kafka extra is required for this provider. "
        "Install it with: pip install hike[kafka]"
    ) from _exc

from hike.events.providers.kafka.event_bus import KafkaEventBus
from hike.events.providers.kafka.publisher import KafkaEventPublisher
from hike.events.providers.kafka.subscriber import KafkaEventSubscriber

__all__ = [
    "KafkaEventBus",
    "KafkaEventPublisher",
    "KafkaEventSubscriber",
]
