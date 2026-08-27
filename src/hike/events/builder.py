from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from hike.events.providers.repository import RepositoryEventPublisher, RepositoryEventSubscriber
from hike.events.interfaces import IEventPublisher
from hike.events.interfaces.subscriber import IExternalEventSubscriber
from hike.events.transactional_box import InboxEventSubscriber, OutboxEventPublisher, TransactionalBox
from hike.persistence.repository import IRepository
from hike.persistence.uow import UnitOfWork

if TYPE_CHECKING:
    from confluent_kafka import Consumer, Producer
    from pika.adapters.blocking_connection import BlockingChannel
    from redis import Redis


# TODO: there should be one FACTORY for outbox and one for inbox. each returning the sub/pub interface
# TODO: according to following url, I can decorate the inbox/outbox with a mapper(which in turn return the same interface
# https://medium.com/@serhatalftkn/domain-events-vs-integration-events-understanding-the-differences-and-when-to-use-each-3977278034d3
class TransactionalBoxBuilder:
    """Fluent builder for :class:`TransactionalBox`.

    Supply the inbox/outbox persistence repositories, pick a broker, then call
    :meth:`build`::

        box = (
            TransactionalBoxBuilder()
            .inbox_repository(my_inbox_repo, my_inbox_uow)
            .outbox_repository(my_outbox_repo, my_outbox_uow)
            .with_kafka(producer=producer, consumer=consumer)
            .build()
        )

    For a custom broker, supply the components directly::

        box = (
            TransactionalBoxBuilder()
            .inbox_repository(my_inbox_repo, my_inbox_uow)
            .outbox_repository(my_outbox_repo, my_outbox_uow)
            .broker_publisher(my_publisher)
            .broker_subscriber(my_subscriber)
            .build()
        )
    """

    def __init__(self) -> None:
        self._inbox_repo: IRepository[Any, Any, Any] | None = None
        self._inbox_uow: UnitOfWork[Any] | None = None
        self._outbox_repo: IRepository[Any, Any, Any] | None = None
        self._outbox_uow: UnitOfWork[Any] | None = None
        self._broker_publisher: IEventPublisher[Any] | None = None
        self._broker_subscriber: IExternalEventSubscriber[Any] | None = None

    # -------------------------------------------------------------------------
    # Low-level API — bring your own components
    # -------------------------------------------------------------------------

    def inbox_repository[TSession](self, repo: IRepository[Any, Any, TSession], uow: UnitOfWork[TSession]) -> Self:
        """Set the persistence repository and unit-of-work used for the inbox."""
        self._inbox_repo = repo
        self._inbox_uow = uow
        return self

    def outbox_repository[TSession](self, repo: IRepository[Any, Any, TSession], uow: UnitOfWork[TSession]) -> Self:
        """Set the persistence repository and unit-of-work used for the outbox."""
        self._outbox_repo = repo
        self._outbox_uow = uow
        return self

    def broker_publisher(self, publisher: IEventPublisher[Any]) -> Self:
        """Set the broker publisher used to forward outbox events."""
        self._broker_publisher = publisher
        return self

    def broker_subscriber(self, subscriber: IExternalEventSubscriber[Any]) -> Self:
        """Set the broker subscriber used to receive events into the inbox."""
        self._broker_subscriber = subscriber
        return self

    # -------------------------------------------------------------------------
    # Broker shortcuts
    # -------------------------------------------------------------------------

    def with_kafka(
            self,
            producer: Producer,
            consumer: Consumer,
            *,
            topic_prefix: str = "hike",
    ) -> Self:
        """Use Kafka as the event broker.

        Requires ``pip install hike[kafka]``.

        :param producer: A :class:`confluent_kafka.Producer` instance.
        :param consumer: A :class:`confluent_kafka.Consumer` instance configured
            with ``enable.auto.commit=false``.
        :param topic_prefix: Topic name prefix; each event type is published to
            ``{topic_prefix}.{EventTypeName}``.
        """
        from hike.events.providers.kafka.publisher import KafkaEventPublisher
        from hike.events.providers.kafka.subscriber import KafkaEventSubscriber
        self._broker_publisher = KafkaEventPublisher(producer, topic_prefix)
        self._broker_subscriber = KafkaEventSubscriber(consumer, topic_prefix)
        return self

    def with_rabbitmq(
            self,
            pub_channel: BlockingChannel,
            sub_channel: BlockingChannel,
            *,
            exchange: str = "hike.events",
            queue: str = "",
    ) -> Self:
        """Use RabbitMQ as the event broker.

        Requires ``pip install hike[rabbitmq]``.

        :param pub_channel: A :class:`pika.adapters.blocking_connection.BlockingChannel`
            used for publishing.
        :param sub_channel: A separate channel used for subscribing.
        :param exchange: Name of the topic exchange to declare / bind against.
        :param queue: Consumer queue name; defaults to a name derived from the
            subscriber class (competing-consumer pattern).
        """
        from hike.events.providers.rabbitmq.publisher import RabbitMQEventPublisher
        from hike.events.providers.rabbitmq.subscriber import RabbitMQEventSubscriber
        self._broker_publisher = RabbitMQEventPublisher(pub_channel, exchange)
        self._broker_subscriber = RabbitMQEventSubscriber(sub_channel, exchange, queue)
        return self

    def with_redis_broker(
            self,
            client: Redis,
            *,
            stream_prefix: str = "hike",
    ) -> Self:
        """Use Redis Streams as the event broker.

        Requires ``pip install hike[redis]``.

        :param client: A :class:`redis.Redis` client instance.
        :param stream_prefix: Stream name prefix; each event type uses its own
            stream ``{stream_prefix}.{EventTypeName}``.
        """
        from hike.events.providers.redis.publisher import RedisEventPublisher
        from hike.events.providers.redis.subscriber import RedisEventSubscriber
        self._broker_publisher = RedisEventPublisher(client, stream_prefix)
        self._broker_subscriber = RedisEventSubscriber(client, stream_prefix)
        return self

    # -------------------------------------------------------------------------
    # Assembly
    # -------------------------------------------------------------------------

    def build(self) -> TransactionalBox:
        """Assemble and return the configured :class:`TransactionalBox`.

        :raises ValueError: if any required component has not been configured.
        """
        if self._inbox_repo is None or self._inbox_uow is None:
            raise ValueError(
                "Inbox repository not configured — call inbox_repository() or with_in_memory_inbox()"
            )
        if self._outbox_repo is None or self._outbox_uow is None:
            raise ValueError(
                "Outbox repository not configured — call outbox_repository() or with_in_memory_outbox()"
            )
        if self._broker_publisher is None:
            raise ValueError(
                "Broker publisher not configured — call broker_publisher() or "
                "with_kafka() / with_rabbitmq() / with_redis_broker()"
            )
        if self._broker_subscriber is None:
            raise ValueError(
                "Broker subscriber not configured — call broker_subscriber() or "
                "with_kafka() / with_rabbitmq() / with_redis_broker()"
            )

        inbox = InboxEventSubscriber(
            broker_subscriber=self._broker_subscriber,
            repo_publisher=RepositoryEventPublisher(self._inbox_repo),
            repo_subscriber=RepositoryEventSubscriber(self._inbox_repo, self._inbox_uow),
        )
        outbox = OutboxEventPublisher(
            repo_publisher=RepositoryEventPublisher(self._outbox_repo),
            repo_subscriber=RepositoryEventSubscriber(self._outbox_repo, self._outbox_uow),
            broker_publisher=self._broker_publisher,
        )
        return TransactionalBox(inbox, outbox)
