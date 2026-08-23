from __future__ import annotations

from typing import Iterable

from confluent_kafka import KafkaError, KafkaException, Producer

from hike.domain_event import DomainEvent, serialize_event
from hike.events.interfaces import IEventPublisher


class KafkaEventPublisher(IEventPublisher[DomainEvent]):
    """Publishes domain events to Kafka.

    Each event type is published to its own topic: ``{topic_prefix}.{EventTypeName}``.
    A :exc:`confluent_kafka.KafkaException` is raised if the broker fails to
    acknowledge any message.

    Install with: ``pip install hike[kafka]``
    """

    def __init__(self, producer: Producer, topic_prefix: str = "hike") -> None:
        self._producer = producer
        self._topic_prefix = topic_prefix

    def publish(self, events: Iterable[DomainEvent]) -> None:
        delivery_errors: list[KafkaError] = []

        def _on_delivery(err: KafkaError | None, _: object) -> None:
            if err is not None:
                delivery_errors.append(err)

        for event in events:
            event_type, json_data = serialize_event(event)
            topic = f"{self._topic_prefix}.{event_type}"
            self._producer.produce(
                topic,
                value=json_data.encode(),
                headers={"event_type": event_type},
                on_delivery=_on_delivery,
            )
        self._producer.flush()

        if delivery_errors:
            raise KafkaException(delivery_errors[0])
