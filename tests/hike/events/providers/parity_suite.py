"""
Abstract parity test suites for event providers.

``EventProviderParitySuite`` — tests ``IEventPublisher`` + ``IExternalEventSubscriber``
as separate objects.  Every concrete provider must have a test class that
inherits from it and provides these fixtures:

- ``publisher``       — an ``IEventPublisher[IntegrationEvent]`` wired to the broker
- ``make_subscriber`` — a *callable* returning a fresh ``IExternalEventSubscriber``
                        each call, always connected to the **same** broker resource
                        so multiple instances act as competing consumers.

``EventBusParitySuite`` — tests ``IExternalEventBus`` as a unified publish+subscribe
object.  Every concrete provider must also have a test class that inherits from
it and provides:

- ``make_event_bus``  — a *callable* returning a fresh ``IExternalEventBus``
                        each call, always wired to the **same** broker resource.

The base classes do **not** declare these fixtures — pytest resolves them by name
at collection time.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from hike.events.integration_event import IntegrationEvent
from hike.events.interfaces import IExternalEventSubscriber, IEventHandler, IEventPublisher
from hike.events.interfaces.event_bus import IExternalEventBus


@dataclass(frozen=True, kw_only=True, eq=False)
class _PingEvent(IntegrationEvent):
    payload: str
    source: ClassVar[str] = "//test-service"
    version: int = 1


class EventProviderParitySuite:

    def _run_subscriber(
        self, subscriber: IExternalEventSubscriber[IntegrationEvent], *, delay: float = 0.15
    ) -> threading.Thread:
        """Start *subscriber* in a daemon thread and return after *delay* seconds.

        The delay lets the subscriber register its broker subscription before
        the test publishes messages.  All three providers are safe with 0.15 s.
        Kafka retains messages so late-joining consumers still receive them.
        """
        t = threading.Thread(target=subscriber.tasks()[0], daemon=True)
        t.start()
        time.sleep(delay)
        return t

    # ------------------------------------------------------------------
    # Existing smoke tests
    # ------------------------------------------------------------------

    def test_publish_then_receive(
        self,
        publisher: IEventPublisher[IntegrationEvent],
        make_subscriber: Callable[[], IExternalEventSubscriber[IntegrationEvent]],
    ) -> None:
        sub = make_subscriber()
        received: list[_PingEvent] = []

        class _Handler(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:
                received.append(event)
                sub.cleanup()

        sub.subscribe(_Handler())  # type: ignore[arg-type]
        t = self._run_subscriber(sub)
        publisher.publish([_PingEvent(payload="hello")])
        t.join(timeout=30)

        assert not t.is_alive(), "subscriber did not stop — no message received within 30 s"
        assert len(received) == 1
        assert received[0].payload == "hello"

    def test_multiple_handlers_all_called(
        self,
        publisher: IEventPublisher[IntegrationEvent],
        make_subscriber: Callable[[], IExternalEventSubscriber[IntegrationEvent]],
    ) -> None:
        sub = make_subscriber()
        calls: list[str] = []

        class _H1(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:
                calls.append("h1")

        class _H2(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:
                calls.append("h2")
                sub.cleanup()

        sub.subscribe(_H1())  # type: ignore[arg-type]
        sub.subscribe(_H2())  # type: ignore[arg-type]
        t = self._run_subscriber(sub)
        publisher.publish([_PingEvent(payload="ping")])
        t.join(timeout=30)

        assert not t.is_alive()
        assert "h1" in calls
        assert "h2" in calls

    def test_close_stops_subscriber(
        self,
        make_subscriber: Callable[[], IExternalEventSubscriber[IntegrationEvent]],
    ) -> None:
        sub = make_subscriber()

        class _Noop(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:
                pass

        sub.subscribe(_Noop())  # type: ignore[arg-type]
        t = self._run_subscriber(sub)
        sub.cleanup()
        t.join(timeout=10)
        assert not t.is_alive()

    def test_handler_failure_does_not_stop_subscriber(
        self,
        publisher: IEventPublisher[IntegrationEvent],
        make_subscriber: Callable[[], IExternalEventSubscriber[IntegrationEvent]],
    ) -> None:
        """A failing handler must not kill the subscriber loop."""
        sub = make_subscriber()
        received: list[_PingEvent] = []
        attempts = 0

        class _FlakyHandler(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RuntimeError("deliberate first-attempt failure")
                received.append(event)
                sub.cleanup()

        sub.subscribe(_FlakyHandler())  # type: ignore[arg-type]
        t = self._run_subscriber(sub)
        publisher.publish([_PingEvent(payload="ping")])
        # Publish a second event so the subscriber has something to consume
        # if the broker does not redeliver the failed message in-session.
        time.sleep(0.3)
        publisher.publish([_PingEvent(payload="ping")])
        t.join(timeout=30)

        assert not t.is_alive(), "subscriber did not stop — may have crashed on handler error"
        assert received

    # ------------------------------------------------------------------
    # Contract 1 — Ack / nack
    # ------------------------------------------------------------------

    def test_ack_nack_contract(
        self,
        publisher: IEventPublisher[IntegrationEvent],
        make_subscriber: Callable[[], IExternalEventSubscriber[IntegrationEvent]],
    ) -> None:
        """A failing handler must NOT ack the message.

        The message must be held by the broker and redelivered so that a new
        subscriber (or the same one after a restart) can retry it.  This is
        verified by running two subscribers sequentially on the same broker
        resource: the first fails and exits, the second processes successfully.
        """
        sub1 = make_subscriber()

        class _FailingHandler(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:
                sub1.cleanup()
                raise RuntimeError("deliberate failure — must not ack")

        sub1.subscribe(_FailingHandler())  # type: ignore[arg-type]
        t1 = self._run_subscriber(sub1)
        publisher.publish([_PingEvent(payload="retry")])
        t1.join(timeout=30)
        assert not t1.is_alive()

        # Give brokers time to make the unacked message available again.
        # For Redis this must exceed claim_idle_ms of the make_subscriber fixture.
        time.sleep(0.5)

        received: list[_PingEvent] = []
        sub2 = make_subscriber()

        class _SuccessHandler(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:
                received.append(event)
                sub2.cleanup()

        sub2.subscribe(_SuccessHandler())  # type: ignore[arg-type]
        t2 = self._run_subscriber(sub2)
        t2.join(timeout=30)

        assert not t2.is_alive(), "sub2 never received the redelivered message"
        assert len(received) == 1

    # ------------------------------------------------------------------
    # Contract 2 — Exclusive in-flight delivery
    # ------------------------------------------------------------------

    def test_exclusive_inflight_contract(
        self,
        publisher: IEventPublisher[IntegrationEvent],
        make_subscriber: Callable[[], IExternalEventSubscriber[IntegrationEvent]],
    ) -> None:
        """Two concurrent instances must not both process the same message.

        Both subscribers share the same broker resource (queue / consumer
        group).  Only one must receive the message; the other must never see
        it, even if both are running at the same time.
        """
        sub1 = make_subscriber()
        sub2 = make_subscriber()
        processed_by: list[str] = []
        finished = threading.Event()

        class _H1(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:
                processed_by.append("sub1")
                sub1.cleanup()
                finished.set()

        class _H2(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:
                processed_by.append("sub2")
                sub2.cleanup()
                finished.set()

        sub1.subscribe(_H1())  # type: ignore[arg-type]
        sub2.subscribe(_H2())  # type: ignore[arg-type]
        t1 = self._run_subscriber(sub1)
        t2 = self._run_subscriber(sub2)
        time.sleep(0.5)  # allow competing-consumer registration to settle

        publisher.publish([_PingEvent(payload="exclusive")])
        assert finished.wait(timeout=30), "no subscriber processed the message within 30 s"
        time.sleep(1.0)  # give the losing subscriber time to incorrectly process

        sub1.cleanup()
        sub2.cleanup()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(processed_by) == 1, (
            f"both subscribers processed the same message: {processed_by}"
        )

    # ------------------------------------------------------------------
    # Contract 3 — Deduplication by event id
    # ------------------------------------------------------------------

    def test_deduplication_contract(
        self,
        publisher: IEventPublisher[IntegrationEvent],
        make_subscriber: Callable[[], IExternalEventSubscriber[IntegrationEvent]],
    ) -> None:
        """Publishing the same event id twice must invoke the handler exactly once.

        The subscriber deduplicates via its in-memory ``_seen_ids`` set.
        The second broker delivery carries a different broker message ID but
        the same ``event.id``; the subscriber must skip it.
        """
        sub = make_subscriber()
        call_count = 0
        first_received = threading.Event()

        class _CountingHandler(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:
                nonlocal call_count
                call_count += 1
                first_received.set()

        sub.subscribe(_CountingHandler())  # type: ignore[arg-type]
        t = self._run_subscriber(sub)

        shared_id = uuid.uuid4()
        publisher.publish([_PingEvent(payload="first", id=shared_id)])
        publisher.publish([_PingEvent(payload="second", id=shared_id)])

        assert first_received.wait(timeout=15), "subscriber did not receive the event"
        time.sleep(1.5)  # allow the duplicate to arrive and (incorrectly) be processed
        sub.cleanup()
        t.join(timeout=5)

        assert call_count == 1, f"expected 1 (dedup), got {call_count}"


class EventBusParitySuite:
    """Smoke tests for IExternalEventBus as a unified publish + subscribe interface."""

    def _run_bus(
        self, bus: IExternalEventBus[IntegrationEvent], *, delay: float = 0.15
    ) -> threading.Thread:
        t = threading.Thread(target=bus.tasks()[0], daemon=True)
        t.start()
        time.sleep(delay)
        return t

    def test_bus_publish_then_receive(
        self,
        make_event_bus: Callable[[], IExternalEventBus[IntegrationEvent]],
    ) -> None:
        """Publishing through the bus reaches a handler subscribed on the same bus."""
        bus = make_event_bus()
        received: list[_PingEvent] = []

        class _Handler(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:
                received.append(event)
                bus.cleanup()

        bus.subscribe(_Handler())  # type: ignore[arg-type]
        t = self._run_bus(bus)
        bus.publish([_PingEvent(payload="bus-hello")])
        t.join(timeout=30)

        assert not t.is_alive(), "bus did not stop — no message received within 30 s"
        assert len(received) == 1
        assert received[0].payload == "bus-hello"

    def test_bus_cleanup_stops_consuming(
        self,
        make_event_bus: Callable[[], IExternalEventBus[IntegrationEvent]],
    ) -> None:
        """cleanup() stops the bus's background task."""
        bus = make_event_bus()

        class _Noop(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:
                pass

        bus.subscribe(_Noop())  # type: ignore[arg-type]
        t = self._run_bus(bus)
        bus.cleanup()
        t.join(timeout=10)
        assert not t.is_alive()
