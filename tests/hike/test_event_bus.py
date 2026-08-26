"""Tests for InMemoryEventBus (hike.events.event_bus)."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from hike.domain_event import DomainEvent
from hike.events.event_bus import EventBus
from hike.events.interfaces import IEventHandler, IReversibleEventHandler

# ---------------------------------------------------------------------------
# Minimal event types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderPlaced(DomainEvent):
    order_id: str


@dataclass(frozen=True)
class OrderShipped(DomainEvent):
    order_id: str


# ---------------------------------------------------------------------------
# Reusable handler implementations
# ---------------------------------------------------------------------------


class _RecordingHandler(IEventHandler[OrderPlaced]):
    def __init__(self) -> None:
        self.received: list[OrderPlaced] = []

    def handle(self, event: OrderPlaced) -> None:
        self.received.append(event)


class _RecordingReversibleHandler(IReversibleEventHandler[OrderPlaced]):
    def __init__(self) -> None:
        self.handled: list[OrderPlaced] = []
        self.compensations: int = 0

    def handle(self, event: OrderPlaced) -> None:
        self.handled.append(event)

    def compensate(self) -> None:
        self.compensations += 1


class _FailingHandler(IEventHandler[OrderPlaced]):
    def handle(self, event: OrderPlaced) -> None:
        raise RuntimeError("regular handler failed")


# ---------------------------------------------------------------------------
# subscribe
# ---------------------------------------------------------------------------


def test_subscribe_registers_handler_under_correct_event_type() -> None:
    bus = EventBus()
    handler = _RecordingHandler()
    bus.subscribe(handler)
    assert handler in bus._regular[OrderPlaced]  # pyright: ignore[reportPrivateUsage]


def test_subscribe_multiple_handlers_for_same_event() -> None:
    bus = EventBus()
    h1, h2 = _RecordingHandler(), _RecordingHandler()
    bus.subscribe(h1)
    bus.subscribe(h2)
    assert bus._regular[OrderPlaced] == [h1, h2]  # pyright: ignore[reportPrivateUsage]


def test_subscribe_different_event_types_are_independent() -> None:
    class _ShipHandler(IEventHandler[OrderShipped]):
        def handle(self, event: OrderShipped) -> None: ...

    bus = EventBus()
    h_placed = _RecordingHandler()
    h_shipped = _ShipHandler()
    bus.subscribe(h_placed)
    bus.subscribe(h_shipped)

    assert bus._regular[OrderPlaced] == [h_placed]  # pyright: ignore[reportPrivateUsage]
    assert bus._regular[OrderShipped] == [h_shipped]  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# produce — basic dispatch
# ---------------------------------------------------------------------------


def test_produce_dispatches_matching_event() -> None:
    bus = EventBus()
    handler = _RecordingHandler()
    bus.subscribe(handler)
    event = OrderPlaced(order_id="1")
    bus.publish([event])
    assert handler.received == [event]


def test_produce_does_not_dispatch_to_wrong_event_type() -> None:
    class _ShipHandler(IEventHandler[OrderShipped]):
        def __init__(self) -> None:
            self.received: list[OrderShipped] = []

        def handle(self, event: OrderShipped) -> None:
            self.received.append(event)

    bus = EventBus()
    placed = _RecordingHandler()
    shipped = _ShipHandler()
    bus.subscribe(placed)
    bus.subscribe(shipped)

    bus.publish([OrderPlaced(order_id="1")])

    assert placed.received
    assert not shipped.received


def test_produce_dispatches_each_event_independently() -> None:
    bus = EventBus()
    handler = _RecordingHandler()
    bus.subscribe(handler)
    events = [OrderPlaced(order_id="1"), OrderPlaced(order_id="2")]
    bus.publish(events)
    assert handler.received == events


def test_produce_with_no_subscription_is_noop() -> None:
    bus = EventBus()
    bus.publish([OrderPlaced(order_id="x")])  # must not raise


def test_produce_calls_all_subscribed_handlers() -> None:
    bus = EventBus()
    h1, h2 = _RecordingHandler(), _RecordingHandler()
    bus.subscribe(h1)
    bus.subscribe(h2)
    event = OrderPlaced(order_id="x")
    bus.publish([event])
    assert h1.received == [event]
    assert h2.received == [event]


# ---------------------------------------------------------------------------
# produce — reversible runs before regular
# ---------------------------------------------------------------------------


def test_reversible_handlers_run_before_regular_handlers() -> None:
    call_order: list[str] = []

    class _Rev(IReversibleEventHandler[OrderPlaced]):
        def handle(self, event: OrderPlaced) -> None:
            call_order.append("reversible")

        def compensate(self) -> None:
            pass

    class _Reg(IEventHandler[OrderPlaced]):
        def handle(self, event: OrderPlaced) -> None:
            call_order.append("regular")

    bus = EventBus()
    bus.subscribe(_Reg())   # subscribed first — but should run second
    bus.subscribe(_Rev())
    bus.publish([OrderPlaced(order_id="x")])

    assert call_order.index("reversible") < call_order.index("regular")


# ---------------------------------------------------------------------------
# produce — compensation on failure
# ---------------------------------------------------------------------------


def test_succeeded_reversible_is_compensated_when_regular_fails() -> None:
    bus = EventBus()
    reversible = _RecordingReversibleHandler()
    bus.subscribe(reversible)
    bus.subscribe(_FailingHandler())

    with pytest.raises(RuntimeError):
        bus.publish([OrderPlaced(order_id="x")])

    assert reversible.compensations == 1


def test_original_exception_is_reraised_after_compensation() -> None:
    bus = EventBus()
    bus.subscribe(_RecordingReversibleHandler())
    bus.subscribe(_FailingHandler())

    with pytest.raises(RuntimeError, match="regular handler failed"):
        bus.publish([OrderPlaced(order_id="x")])


def test_reversible_handler_that_fails_in_handle_is_not_compensated() -> None:
    compensation_called = False

    class _FailRev(IReversibleEventHandler[OrderPlaced]):
        def handle(self, event: OrderPlaced) -> None:
            raise RuntimeError("boom")

        def compensate(self) -> None:
            nonlocal compensation_called
            compensation_called = True

    bus = EventBus()
    bus.subscribe(_FailRev())

    with pytest.raises(RuntimeError):
        bus.publish([OrderPlaced(order_id="x")])

    assert not compensation_called


def test_compensation_runs_in_reverse_subscription_order() -> None:
    compensation_order: list[int] = []

    def _make_reversible(n: int) -> IReversibleEventHandler[OrderPlaced]:
        class _H(IReversibleEventHandler[OrderPlaced]):
            def handle(self, event: OrderPlaced) -> None:
                pass

            def compensate(self) -> None:
                compensation_order.append(n)

        return _H()

    bus = EventBus()
    bus.subscribe(_make_reversible(1))
    bus.subscribe(_make_reversible(2))
    bus.subscribe(_make_reversible(3))
    bus.subscribe(_FailingHandler())

    with pytest.raises(RuntimeError):
        bus.publish([OrderPlaced(order_id="x")])

    assert compensation_order == [3, 2, 1]


def test_regular_handlers_are_not_compensated() -> None:
    """Plain IEventHandler subclasses run normally; no compensate is attempted on them."""
    bus = EventBus()
    regular = _RecordingHandler()
    bus.subscribe(regular)
    bus.subscribe(_FailingHandler())

    event = OrderPlaced(order_id="x")
    with pytest.raises(RuntimeError):
        bus.publish([event])

    # regular ran before the failing handler; assert it received the event
    assert regular.received == [event]
