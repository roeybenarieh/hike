"""Tests for CrossAggregateRule, authority decorator, InMemoryEventBus, and DomainService."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from hike.aggregate import UuidAggregate, authority
from hike.domain_event import DomainEvent
from hike.entity import Field
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
