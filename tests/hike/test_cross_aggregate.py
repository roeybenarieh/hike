"""Tests for CrossAggregateRule, EventBus/InMemoryEventBus, and ProcessManager."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from hike.aggregate import UuidAggregate, authority
from hike.domain_event import DomainEvent, EventBus, InMemoryEventBus
from hike.domain_service import DomainService
from hike.entity import Field
from hike.process_manager import ProcessManager
from hike.rules import CrossAggregateRule, RuleBrokenError
from hike.value_object import ValueObject


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class MemberCount(ValueObject[int]): ...
class MaxMembers(ValueObject[int]): ...


class Tenant(UuidAggregate):
    member_count: Field[MemberCount]
    max_members: Field[MaxMembers]

    @authority
    def can_add_member(self) -> bool:
        return self.member_count.value < self.max_members.value


class Member(UuidAggregate):
    pass


@dataclass
class TenantMemberContext:
    tenant: Tenant
    member: Member


class MemberLimitRule(CrossAggregateRule[TenantMemberContext]):
    def is_broken(self, context: TenantMemberContext) -> bool:
        return not context.tenant.can_add_member()


class OrderPlaced(DomainEvent): ...
class PaymentRequested(DomainEvent): ...


# ---------------------------------------------------------------------------
# CrossAggregateRule
# ---------------------------------------------------------------------------


class TestCrossAggregateRule:
    def test_check_raises_when_broken(self) -> None:
        tenant = Tenant(member_count=MemberCount(5), max_members=MaxMembers(5))
        member = Member()
        ctx = TenantMemberContext(tenant=tenant, member=member)

        with pytest.raises(RuleBrokenError) as exc_info:
            MemberLimitRule().check(ctx)

        assert exc_info.value.broken_rule is not None

    def test_check_silent_when_not_broken(self) -> None:
        tenant = Tenant(member_count=MemberCount(3), max_members=MaxMembers(5))
        member = Member()
        ctx = TenantMemberContext(tenant=tenant, member=member)

        MemberLimitRule().check(ctx)  # must not raise

    def test_broken_rule_stored_on_error(self) -> None:
        tenant = Tenant(member_count=MemberCount(5), max_members=MaxMembers(5))
        ctx = TenantMemberContext(tenant=tenant, member=Member())
        rule = MemberLimitRule()

        with pytest.raises(RuleBrokenError) as exc_info:
            rule.check(ctx)

        assert exc_info.value.broken_rule is rule


# ---------------------------------------------------------------------------
# @authority decorator
# ---------------------------------------------------------------------------


class TestAuthorityDecorator:
    def test_marks_method_with_flag(self) -> None:
        assert getattr(Tenant.can_add_member, "_is_authority_check", False) is True

    def test_decorated_method_still_callable(self) -> None:
        tenant = Tenant(member_count=MemberCount(2), max_members=MaxMembers(3))
        assert tenant.can_add_member() is True

        full_tenant = Tenant(member_count=MemberCount(3), max_members=MaxMembers(3))
        assert full_tenant.can_add_member() is False


# ---------------------------------------------------------------------------
# InMemoryEventBus
# ---------------------------------------------------------------------------


class TestInMemoryEventBus:
    def test_publish_dispatches_to_subscriber(self) -> None:
        bus = InMemoryEventBus()
        received: list[OrderPlaced] = []

        bus.subscribe(OrderPlaced, received.append)
        bus.publish(OrderPlaced())

        assert len(received) == 1
        assert isinstance(received[0], OrderPlaced)

    def test_publish_does_not_dispatch_to_wrong_type(self) -> None:
        bus = InMemoryEventBus()
        received: list[PaymentRequested] = []

        bus.subscribe(PaymentRequested, received.append)
        bus.publish(OrderPlaced())  # different type

        assert received == []

    def test_multiple_subscribers_for_same_event(self) -> None:
        bus = InMemoryEventBus()
        calls_a: list[DomainEvent] = []
        calls_b: list[DomainEvent] = []

        bus.subscribe(OrderPlaced, calls_a.append)
        bus.subscribe(OrderPlaced, calls_b.append)
        bus.publish(OrderPlaced())

        assert len(calls_a) == 1
        assert len(calls_b) == 1

    def test_publish_all_dispatches_each_event(self) -> None:
        bus = InMemoryEventBus()
        received: list[DomainEvent] = []

        bus.subscribe(OrderPlaced, received.append)
        bus.publish_all([OrderPlaced(), OrderPlaced()])

        assert len(received) == 2

    def test_no_subscribers_publish_is_silent(self) -> None:
        bus = InMemoryEventBus()
        bus.publish(OrderPlaced())  # must not raise


# ---------------------------------------------------------------------------
# ProcessManager
# ---------------------------------------------------------------------------


class TestProcessManager:
    def test_subscribe_called_at_construction(self) -> None:
        bus = InMemoryEventBus()
        subscribe_calls: list[None] = []

        class TrackingManager(ProcessManager):
            def _subscribe(self) -> None:
                subscribe_calls.append(None)

        TrackingManager(bus)

        assert len(subscribe_calls) == 1

    def test_handler_invoked_on_matching_event(self) -> None:
        bus = InMemoryEventBus()
        handled: list[OrderPlaced] = []

        class OrderManager(ProcessManager):
            def _subscribe(self) -> None:
                self._bus.subscribe(OrderPlaced, self._on_order_placed)

            def _on_order_placed(self, event: OrderPlaced) -> None:
                handled.append(event)

        OrderManager(bus)
        event = OrderPlaced()
        bus.publish(event)

        assert len(handled) == 1
        assert handled[0] is event

    def test_handler_not_invoked_for_different_event(self) -> None:
        bus = InMemoryEventBus()
        handled: list[DomainEvent] = []

        class OrderManager(ProcessManager):
            def _subscribe(self) -> None:
                self._bus.subscribe(OrderPlaced, handled.append)

        OrderManager(bus)
        bus.publish(PaymentRequested())  # different type

        assert handled == []


# ---------------------------------------------------------------------------
# DomainService (marker only — just confirms it is importable and abstract)
# ---------------------------------------------------------------------------


class TestDomainService:
    def test_is_importable_and_subclassable(self) -> None:
        class MyService(DomainService):
            pass

        MyService()  # must not raise

    def test_is_abstract_base(self) -> None:
        from abc import ABC
        assert issubclass(DomainService, ABC)
