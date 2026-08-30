from __future__ import annotations

from typing import Iterable

from confluent_kafka import Consumer, Producer

from hike.events.integration_event import IntegrationEvent
from hike.events.interfaces.event_bus import IExternalEventBus
from hike.events.providers.kafka.publisher import KafkaEventPublisher
from hike.events.providers.kafka.subscriber import KafkaEventSubscriber


class KafkaEventBus(KafkaEventSubscriber, IExternalEventBus[IntegrationEvent]):
    """Combined publisher + subscriber for Kafka.

    Publishes to ``{topic_prefix}.{EventType}`` topics and consumes from the
    same topic pattern.  The *consumer* must be configured with
    ``enable.auto.commit=false``; offsets are committed only after all handlers
    succeed.

    Install with: ``pip install hike[kafka]``
    """

    def __init__(
            self,
            producer: Producer,
            consumer: Consumer,
            topic_prefix: str = "hike",
    ) -> None:
        super().__init__(consumer, topic_prefix)
        self._publisher = KafkaEventPublisher(producer, topic_prefix)

    def publish(self, events: Iterable[IntegrationEvent]) -> None:
        self._publisher.publish(events)
