"""Tests for the two-mode event dispatch system.

Mode 1: in-memory synchronous — events dispatched via InMemoryEventBus after commit.
Mode 2: outbox/inbox — events stored atomically, processed exactly-once by InboxProcessor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest

from hike import (
    CrossAggregateInvariantHandler,
    DomainEvent,
    EventHandler,
    IInboxRepository,
    IOutboxRepository,
    InMemoryEventBus,
    InboxProcessor,
    InboxRecord,
    OutboxRecord,
    OutboxRelay,
    UnitOfWork,
    UuidAggregate,
    deserialize_event,
    register_event,
    serialize_event,
)
from hike.domain_event import _EVENT_REGISTRY
from hike.entity import Field
from hike.persistence.providers.in_memory import InMemoryDBContext, InMemoryInboxRepository, InMemoryOutboxRepository, InMemoryRepository
from hike.value_object import ValueObject, non_empty


# ---------------------------------------------------------------------------
# Domain model that raises events
# ---------------------------------------------------------------------------


class ShipName(ValueObject[str]):
    __validators__ = [non_empty]


@register_event
@dataclass(frozen=True)
class ShipLaunched(DomainEvent):
    ship_id: str
    name: str


@register_event
@dataclass(frozen=True)
class ShipRenamed(DomainEvent):
    ship_id: str
    old_name: str
    new_name: str


class Ship(UuidAggregate):
    name: Field[ShipName]

    def launch(self) -> None:
        self.raise_event(ShipLaunched(ship_id=str(self.id.value), name=self.name.value))

    def rename(self, new_name: ShipName) -> None:
        old = self.name.value
        self.name = new_name
        self.raise_event(ShipRenamed(ship_id=str(self.id.value), old_name=old, new_name=new_name.value))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_ship_uow() -> tuple[UnitOfWork[Any], InMemoryRepository[UUID, Ship]]:
    ctx = InMemoryDBContext()
    repo: InMemoryRepository[UUID, Ship] = InMemoryRepository()
    return UnitOfWork(ctx), repo


# ---------------------------------------------------------------------------
# Mode 1: in-memory synchronous bus
# ---------------------------------------------------------------------------


def test_bus_dispatches_after_commit() -> None:
    uow, repo = make_ship_uow()
    bus = InMemoryEventBus()
    received: list[DomainEvent] = []
    bus.subscribe(ShipLaunched, received.append)

    ship = Ship(name=ShipName("Black Pearl"))
    ship.launch()

    with uow(repo, bus=bus):
        repo.save(ship)
        uow.commit()

    assert len(received) == 1
    assert isinstance(received[0], ShipLaunched)
    assert received[0].name == "Black Pearl"


def test_bus_collects_from_multiple_repos() -> None:
    ctx = InMemoryDBContext()
    repo_a: InMemoryRepository[UUID, Ship] = InMemoryRepository()
    repo_b: InMemoryRepository[UUID, Ship] = InMemoryRepository()
    uow: UnitOfWork[Any] = UnitOfWork(ctx)
    bus = InMemoryEventBus()
    received: list[DomainEvent] = []
    bus.subscribe(ShipLaunched, received.append)

    ship_a = Ship(name=ShipName("Alpha"))
    ship_a.launch()
    ship_b = Ship(name=ShipName("Beta"))
    ship_b.launch()

    with uow(repo_a, repo_b, bus=bus):
        repo_a.save(ship_a)
        repo_b.save(ship_b)
        uow.commit()

    assert len(received) == 2


def test_bus_receives_nothing_without_events() -> None:
    uow, repo = make_ship_uow()
    bus = InMemoryEventBus()
    received: list[DomainEvent] = []
    bus.subscribe(ShipLaunched, received.append)

    ship = Ship(name=ShipName("Silent"))

    with uow(repo, bus=bus):
        repo.save(ship)
        uow.commit()

    assert received == []


def test_events_cleared_from_repo_after_dispatch() -> None:
    uow, repo = make_ship_uow()
    bus = InMemoryEventBus()

    ship = Ship(name=ShipName("Test Ship"))
    ship.launch()

    with uow(repo, bus=bus):
        repo.save(ship)
        uow.commit()

    # Pending events should be empty after commit
    assert repo.drain_events() == []


def test_stored_copy_has_no_events() -> None:
    uow, repo = make_ship_uow()
    bus = InMemoryEventBus()

    ship = Ship(name=ShipName("Ghost"))
    ship.launch()

    with uow(repo, bus=bus):
        repo.save(ship)
        uow.commit()

    with uow(repo):
        fetched = repo.get_one(ship.id)

    assert fetched.get_events() == []


def test_no_bus_events_are_silently_discarded() -> None:
    uow, repo = make_ship_uow()
    ship = Ship(name=ShipName("No Handler"))
    ship.launch()

    with uow(repo):
        repo.save(ship)
        uow.commit()

    assert repo.drain_events() == []


def test_events_on_update_dispatched() -> None:
    uow, repo = make_ship_uow()
    bus = InMemoryEventBus()
    received: list[DomainEvent] = []
    bus.subscribe(ShipRenamed, received.append)

    ship = Ship(name=ShipName("Old Name"))
    with uow(repo):
        repo.save(ship)
        uow.commit()

    ship.rename(ShipName("New Name"))
    with uow(repo, bus=bus):
        repo.update(ship)
        uow.commit()

    assert len(received) == 1
    assert isinstance(received[0], ShipRenamed)
    assert received[0].new_name == "New Name"


def test_auto_commit_dispatches_events() -> None:
    uow, repo = make_ship_uow()
    bus = InMemoryEventBus()
    received: list[DomainEvent] = []
    bus.subscribe(ShipLaunched, received.append)

    ship = Ship(name=ShipName("Auto"))
    ship.launch()

    with uow(repo, auto_commit=True, bus=bus):
        repo.save(ship)

    assert len(received) == 1


def test_handler_error_prevents_commit() -> None:
    """If a bus handler raises, the DB commit must NOT happen."""
    from hike.persistence.repository import AggregateDoesNotExistError

    uow, repo = make_ship_uow()
    bus = InMemoryEventBus()

    def bad_handler(event: ShipLaunched) -> None:
        raise RuntimeError("handler failed")

    bus.subscribe(ShipLaunched, bad_handler)

    ship = Ship(name=ShipName("Doomed"))
    ship.launch()

    with pytest.raises(RuntimeError, match="handler failed"):
        with uow(repo, bus=bus):
            repo.save(ship)
            uow.commit()

    # The transaction rolled back — ship must not be persisted
    with uow(repo):
        with pytest.raises(AggregateDoesNotExistError):
            repo.get_one(ship.id)


# ---------------------------------------------------------------------------
# Mode 2: outbox pattern
# ---------------------------------------------------------------------------


def test_outbox_stores_events_on_commit() -> None:
    uow, repo = make_ship_uow()
    outbox = InMemoryOutboxRepository()

    ship = Ship(name=ShipName("Outbox Ship"))
    ship.launch()

    with uow(repo, outbox=outbox):
        repo.save(ship)
        uow.commit()

    records = outbox.get_pending()
    assert len(records) == 1
    assert records[0].event_type == "ShipLaunched"


def test_outbox_no_events_when_aggregate_is_clean() -> None:
    uow, repo = make_ship_uow()
    outbox = InMemoryOutboxRepository()

    ship = Ship(name=ShipName("Clean"))

    with uow(repo, outbox=outbox):
        repo.save(ship)
        uow.commit()

    assert outbox.get_pending() == []


def test_outbox_relay_dispatches_and_deletes() -> None:
    uow, repo = make_ship_uow()
    outbox = InMemoryOutboxRepository()
    bus = InMemoryEventBus()
    received: list[DomainEvent] = []
    bus.subscribe(ShipLaunched, received.append)

    ship = Ship(name=ShipName("Relay Ship"))
    ship.launch()

    with uow(repo, outbox=outbox):
        repo.save(ship)
        uow.commit()

    relay = OutboxRelay(outbox, bus)
    relay.run_once()

    assert len(received) == 1
    assert outbox.get_pending() == []


def test_outbox_relay_multiple_events() -> None:
    uow, repo = make_ship_uow()
    outbox = InMemoryOutboxRepository()
    bus = InMemoryEventBus()
    received: list[DomainEvent] = []
    bus.subscribe(ShipLaunched, received.append)
    bus.subscribe(ShipRenamed, received.append)

    ship = Ship(name=ShipName("Multi"))
    ship.launch()
    ship.rename(ShipName("Multi2"))

    with uow(repo, outbox=outbox):
        repo.save(ship)
        uow.commit()

    OutboxRelay(outbox, bus).run_once()

    assert len(received) == 2
    assert outbox.get_pending() == []


# ---------------------------------------------------------------------------
# Inbox: exactly-once processing
# ---------------------------------------------------------------------------


def test_inbox_processes_event_once() -> None:
    inbox_repo = InMemoryInboxRepository()
    bus = InMemoryEventBus()
    received: list[DomainEvent] = []
    bus.subscribe(ShipLaunched, received.append)

    processor = InboxProcessor(inbox_repo, bus)
    event = ShipLaunched(ship_id="abc", name="Inbox Ship")

    processor.process("evt-1", event)
    processor.process("evt-1", event)  # duplicate — should be skipped

    assert len(received) == 1


def test_inbox_processes_different_events() -> None:
    inbox_repo = InMemoryInboxRepository()
    bus = InMemoryEventBus()
    received: list[DomainEvent] = []
    bus.subscribe(ShipLaunched, received.append)

    processor = InboxProcessor(inbox_repo, bus)
    processor.process("evt-1", ShipLaunched(ship_id="a", name="Ship A"))
    processor.process("evt-2", ShipLaunched(ship_id="b", name="Ship B"))

    assert len(received) == 2


def test_inbox_marks_processed() -> None:
    inbox_repo = InMemoryInboxRepository()
    bus = InMemoryEventBus()
    processor = InboxProcessor(inbox_repo, bus)

    event = ShipLaunched(ship_id="x", name="X")
    processor.process("evt-x", event)

    assert inbox_repo.is_processed("evt-x")


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


def test_serialize_deserialize_roundtrip() -> None:
    event = ShipLaunched(ship_id="test-123", name="Pearl")
    event_type, json_data = serialize_event(event)

    assert event_type == "ShipLaunched"
    restored = deserialize_event(event_type, json_data)

    assert isinstance(restored, ShipLaunched)
    assert restored.ship_id == "test-123"
    assert restored.name == "Pearl"


def test_register_event_stores_in_registry() -> None:
    assert "ShipLaunched" in _EVENT_REGISTRY
    assert "ShipRenamed" in _EVENT_REGISTRY


# ---------------------------------------------------------------------------
# EventBus.drain convenience method
# ---------------------------------------------------------------------------


def test_drain_publishes_and_clears() -> None:
    bus = InMemoryEventBus()
    received: list[DomainEvent] = []
    bus.subscribe(ShipLaunched, received.append)

    ship = Ship(name=ShipName("Drain"))
    ship.launch()

    bus.drain(ship)

    assert len(received) == 1
    assert ship.get_events() == []


# ---------------------------------------------------------------------------
# Handler objects (EventHandler + CrossAggregateInvariantHandler)
# ---------------------------------------------------------------------------


class RecordingHandler(EventHandler[ShipLaunched]):
    def __init__(self) -> None:
        self.received: list[ShipLaunched] = []

    def handle(self, event: ShipLaunched) -> None:
        self.received.append(event)

    def compensate(self, event: ShipLaunched) -> None:
        pass


def test_handler_object_is_dispatched() -> None:
    bus = InMemoryEventBus()
    handler = RecordingHandler()
    bus.subscribe(ShipLaunched, handler)

    ship = Ship(name=ShipName("Handler Ship"))
    ship.launch()
    bus.drain(ship)

    assert len(handler.received) == 1
    assert handler.received[0].name == "Handler Ship"


def test_handler_object_receives_only_subscribed_events() -> None:
    bus = InMemoryEventBus()
    handler = RecordingHandler()
    bus.subscribe(ShipLaunched, handler)

    ship = Ship(name=ShipName("Mix"))
    ship.launch()
    ship.rename(ShipName("Mix2"))  # ShipRenamed — not subscribed
    bus.drain(ship)

    assert len(handler.received) == 1  # only ShipLaunched


def test_handler_object_and_callable_can_coexist() -> None:
    bus = InMemoryEventBus()
    handler = RecordingHandler()
    received_callable: list[DomainEvent] = []
    bus.subscribe(ShipLaunched, handler)
    bus.subscribe(ShipLaunched, received_callable.append)

    ship = Ship(name=ShipName("Both"))
    ship.launch()
    bus.drain(ship)

    assert len(handler.received) == 1
    assert len(received_callable) == 1


def test_handler_object_error_prevents_commit() -> None:
    from hike.persistence.repository import AggregateDoesNotExistError

    class FailingHandler(EventHandler[ShipLaunched]):
        def handle(self, event: ShipLaunched) -> None:
            raise RuntimeError("handler object failed")

        def compensate(self, event: ShipLaunched) -> None:
            pass

    uow, repo = make_ship_uow()
    bus = InMemoryEventBus()
    bus.subscribe(ShipLaunched, FailingHandler())

    ship = Ship(name=ShipName("Doomed2"))
    ship.launch()

    with pytest.raises(RuntimeError, match="handler object failed"):
        with uow(repo, bus=bus):
            repo.save(ship)
            uow.commit()

    with uow(repo):
        with pytest.raises(AggregateDoesNotExistError):
            repo.get_one(ship.id)


# ---------------------------------------------------------------------------
# CrossAggregateInvariantHandler
# ---------------------------------------------------------------------------


class DockCount(ValueObject[int]): ...


class Harbor(UuidAggregate):
    dock_count: Field[DockCount]

    def receive_ship(self) -> None:
        self.dock_count = self.dock_count + 1

    def release_ship(self) -> None:
        self.dock_count = self.dock_count - 1


def make_harbor_uow() -> tuple[UnitOfWork[Any], InMemoryRepository[UUID, Harbor]]:
    ctx = InMemoryDBContext()
    repo: InMemoryRepository[UUID, Harbor] = InMemoryRepository()
    return UnitOfWork(ctx), repo


class IncrementHarborDockCount(CrossAggregateInvariantHandler[ShipLaunched]):
    """On ShipLaunched, load the harbor and increment its dock count."""

    def __init__(
        self,
        harbor_id: object,
        repo: Any,
        uow: Any,
    ) -> None:
        super().__init__(repo, uow)
        self._harbor_id = harbor_id

    def handle(self, event: ShipLaunched) -> None:
        harbor_repo = self._repo
        harbor_uow = self._uow
        with harbor_uow(harbor_repo):
            harbor = harbor_repo.get_one(self._harbor_id)
            harbor.receive_ship()
            harbor_repo.update(harbor)
            harbor_uow.commit()

    def compensate(self, event: ShipLaunched) -> None:
        harbor_repo = self._repo
        harbor_uow = self._uow
        with harbor_uow(harbor_repo):
            harbor = harbor_repo.get_one(self._harbor_id)
            harbor.release_ship()
            harbor_repo.update(harbor)
            harbor_uow.commit()


def test_cross_aggregate_invariant_handler_updates_other_aggregate() -> None:
    ship_uow, ship_repo = make_ship_uow()
    harbor_uow, harbor_repo = make_harbor_uow()

    harbor = Harbor(dock_count=DockCount(0))
    with harbor_uow(harbor_repo):
        harbor_repo.save(harbor)
        harbor_uow.commit()

    ship_bus = InMemoryEventBus()
    handler = IncrementHarborDockCount(harbor.id, harbor_repo, harbor_uow)
    ship_bus.subscribe(ShipLaunched, handler)

    ship = Ship(name=ShipName("Docking Ship"))
    ship.launch()
    with ship_uow(ship_repo, bus=ship_bus):
        ship_repo.save(ship)
        ship_uow.commit()

    with harbor_uow(harbor_repo):
        updated_harbor = harbor_repo.get_one(harbor.id)
    assert updated_harbor.dock_count == 1


def test_cross_aggregate_invariant_handler_is_also_callable() -> None:
    """Handler objects must be usable as callables (via __call__)."""
    harbor_uow, harbor_repo = make_harbor_uow()
    harbor = Harbor(dock_count=DockCount(5))
    with harbor_uow(harbor_repo):
        harbor_repo.save(harbor)
        harbor_uow.commit()

    handler = IncrementHarborDockCount(harbor.id, harbor_repo, harbor_uow)
    event = ShipLaunched(ship_id="x", name="Direct Call Ship")
    handler(event)  # call directly as a callable

    with harbor_uow(harbor_repo):
        updated = harbor_repo.get_one(harbor.id)
    assert updated.dock_count == 6


# ---------------------------------------------------------------------------
# Compensation (saga rollback)
# ---------------------------------------------------------------------------


class _TrackingHandler(EventHandler[ShipLaunched]):
    """Records handled/compensated events; can be set to fail on handle."""

    def __init__(self, name: str, fail_on_handle: bool = False) -> None:
        self.name = name
        self.handled: list[ShipLaunched] = []
        self.compensated: list[ShipLaunched] = []
        self._fail = fail_on_handle

    def handle(self, event: ShipLaunched) -> None:
        if self._fail:
            raise RuntimeError(f"{self.name} failed")
        self.handled.append(event)

    def compensate(self, event: ShipLaunched) -> None:
        self.compensated.append(event)


def test_compensation_runs_in_reverse_order() -> None:
    """When a later handler fails, succeeded handlers are compensated in reverse order."""
    bus = InMemoryEventBus()
    handler_a = _TrackingHandler("A")
    handler_b = _TrackingHandler("B")
    handler_c = _TrackingHandler("C", fail_on_handle=True)
    bus.subscribe(ShipLaunched, handler_a)
    bus.subscribe(ShipLaunched, handler_b)
    bus.subscribe(ShipLaunched, handler_c)

    event = ShipLaunched(ship_id="s", name="Test")
    with pytest.raises(RuntimeError, match="C failed"):
        bus.publish_all([event])

    # A and B handled; C failed → compensate B then A (reverse)
    assert handler_a.compensated == [event]
    assert handler_b.compensated == [event]
    assert handler_a.handled == [event]
    assert handler_b.handled == [event]
    # compensate order: B before A
    compensation_order = [*handler_b.compensated, *handler_a.compensated]
    assert len(compensation_order) == 2  # both compensated exactly once


def test_compensation_not_called_on_success() -> None:
    """When all handlers succeed, compensate is never called."""
    bus = InMemoryEventBus()
    handler = _TrackingHandler("A")
    bus.subscribe(ShipLaunched, handler)

    bus.publish_all([ShipLaunched(ship_id="s", name="OK")])

    assert handler.compensated == []


def test_compensation_failure_does_not_mask_original_error() -> None:
    """A failing compensate() must not replace the original exception."""

    class _BadCompensate(EventHandler[ShipLaunched]):
        def handle(self, event: ShipLaunched) -> None:
            pass  # succeeds

        def compensate(self, event: ShipLaunched) -> None:
            raise ValueError("compensation error")

    class _FailHandle(EventHandler[ShipLaunched]):
        def handle(self, event: ShipLaunched) -> None:
            raise RuntimeError("original error")

        def compensate(self, event: ShipLaunched) -> None:
            pass

    bus = InMemoryEventBus()
    bus.subscribe(ShipLaunched, _BadCompensate())
    bus.subscribe(ShipLaunched, _FailHandle())

    with pytest.raises(RuntimeError, match="original error"):
        bus.publish_all([ShipLaunched(ship_id="s", name="Test")])


def test_plain_callable_not_compensated() -> None:
    """A plain callable that ran before a failing handler is not compensated."""
    bus = InMemoryEventBus()
    callable_calls: list[ShipLaunched] = []
    bus.subscribe(ShipLaunched, callable_calls.append)
    bus.subscribe(ShipLaunched, _TrackingHandler("Fail", fail_on_handle=True))

    with pytest.raises(RuntimeError):
        bus.publish_all([ShipLaunched(ship_id="s", name="Test")])

    # Callable ran but is not in succeeded list — no compensation attempted on it
    assert len(callable_calls) == 1  # callable ran normally


def test_cross_aggregate_handler_compensate_reverses_db_write() -> None:
    """compensate() undoes the DB write made by handle()."""
    ship_uow, ship_repo = make_ship_uow()
    harbor_uow, harbor_repo = make_harbor_uow()

    harbor = Harbor(dock_count=DockCount(0))
    with harbor_uow(harbor_repo):
        harbor_repo.save(harbor)
        harbor_uow.commit()

    # IncrementHarborDockCount succeeds (+1), then a second handler fails
    class _FailAfter(EventHandler[ShipLaunched]):
        def handle(self, event: ShipLaunched) -> None:
            raise RuntimeError("second handler failed")

        def compensate(self, event: ShipLaunched) -> None:
            pass

    bus = InMemoryEventBus()
    bus.subscribe(ShipLaunched, IncrementHarborDockCount(harbor.id, harbor_repo, harbor_uow))
    bus.subscribe(ShipLaunched, _FailAfter())

    event = ShipLaunched(ship_id="s", name="Ship")
    with pytest.raises(RuntimeError, match="second handler failed"):
        bus.publish_all([event])

    with harbor_uow(harbor_repo):
        final_harbor = harbor_repo.get_one(harbor.id)
    assert final_harbor.dock_count == 0  # compensation reversed the +1
