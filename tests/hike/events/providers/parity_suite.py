"""
Abstract parity test suite for IEventPublisher + IBlockingEventSubscriber.

Every concrete event provider should have a test class that inherits from
``EventProviderParitySuite`` and supplies the following pytest fixtures:

- ``publisher``  — an ``IEventPublisher[DomainEvent]`` for the provider
- ``subscriber`` — an ``IBlockingEventSubscriber`` wired to the same channel

The base class does **not** declare them — pytest resolves them by name at
collection time, so subclass fixtures may have any signature without causing
type errors.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from hike.domain_event import DomainEvent, register_event
from hike.events.interfaces import IBlockingEventSubscriber, IEventHandler, IEventPublisher


@register_event
@dataclass(frozen=True)
class _PingEvent(DomainEvent):
    payload: str


class EventProviderParitySuite:
    """
    Parity test suite for IEventPublisher + IBlockingEventSubscriber.

    Subclasses must provide ``publisher`` and ``subscriber`` as pytest fixtures
    wired to the same channel/topic/queue.  The base class does **not** declare
    them — pytest resolves them by name, so subclass fixtures may accept any
    additional fixture parameters without causing type-mismatch linting errors.
    """

    def _run_subscriber(
        self, subscriber: IBlockingEventSubscriber, *, delay: float = 0.15
    ) -> threading.Thread:
        """Start *subscriber* in a daemon thread and sleep *delay* seconds.

        The delay gives the subscriber time to register its channel subscription
        with the broker before the test publishes messages.  All three providers
        (Redis, RabbitMQ, Kafka) are safe with 0.15 s: Redis/Kafka subscriptions
        register almost instantly, and RabbitMQ queues are bound before ``start``
        is even called.  Kafka message retention means late-joining consumers
        still receive messages published before they joined.
        """
        t = threading.Thread(target=subscriber.start, daemon=True)
        t.start()
        time.sleep(delay)
        return t

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_publish_then_receive(
        self,
        publisher: IEventPublisher[DomainEvent],
        subscriber: IBlockingEventSubscriber,
    ) -> None:
        received: list[_PingEvent] = []

        class _Handler(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:
                received.append(event)
                subscriber.close()

        subscriber.subscribe(_Handler())
        t = self._run_subscriber(subscriber)
        publisher.publish([_PingEvent(payload="hello")])
        t.join(timeout=30)

        assert not t.is_alive(), "subscriber did not stop — no message received within 30 s"
        assert len(received) == 1
        assert received[0].payload == "hello"

    def test_multiple_handlers_all_called(
        self,
        publisher: IEventPublisher[DomainEvent],
        subscriber: IBlockingEventSubscriber,
    ) -> None:
        calls: list[str] = []

        class _H1(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:  # noqa: ARG002
                calls.append("h1")

        class _H2(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:  # noqa: ARG002
                calls.append("h2")
                subscriber.close()

        subscriber.subscribe(_H1())
        subscriber.subscribe(_H2())
        t = self._run_subscriber(subscriber)
        publisher.publish([_PingEvent(payload="ping")])
        t.join(timeout=30)

        assert not t.is_alive()
        assert "h1" in calls
        assert "h2" in calls

    def test_close_stops_subscriber(
        self,
        subscriber: IBlockingEventSubscriber,
    ) -> None:
        class _Noop(IEventHandler[DomainEvent]):
            def handle(self, event: DomainEvent) -> None:  # noqa: ARG002
                pass

        subscriber.subscribe(_Noop())
        t = self._run_subscriber(subscriber)
        subscriber.close()
        t.join(timeout=10)
        assert not t.is_alive()
