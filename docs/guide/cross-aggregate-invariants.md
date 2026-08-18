# Cross-Aggregate Invariants

!!! warning "🚧 Work in Progress"
    This page is actively being written. The API and examples are functional, but some sections may be incomplete or revised before the stable release.

A single aggregate enforces its own rules perfectly — you can write `Order(price=Price(-1))` and Hike rejects it immediately. But some business rules span **two or more aggregates**. For example:

- *A `Team` may not exceed its roster size limit.*
- *A user's email must be unique across all `User` aggregates.*
- *A `Payment` cannot be refunded after the `Order` is cancelled.*

These **cross-aggregate invariants** cannot live inside a single aggregate's `__invariants__` list because, by design, an aggregate only sees its own state. This guide explains the patterns Hike provides to handle them.

---

## The Core Tension

DDD's golden rule is **one aggregate per transaction**. Each aggregate is its own consistency boundary — it checks its own rules atomically. When an invariant touches two aggregates, you have two options:

| Option | Trade-off |
| :--- | :--- |
| **Strong consistency** — load both aggregates, check the rule, save in one transaction | Guaranteed at the moment of the check, but a small race window can remain unless the database provides an additional lock. Simpler to reason about. |
| **Eventual consistency** — let one aggregate emit an event; the other reacts asynchronously | Scales better, fully decoupled, but the system is briefly inconsistent between the event being published and the reaction being processed. |

Neither is universally better. The right choice depends on how bad a temporary violation would be for your business. The rest of this guide shows how to implement each.

---

## Overview of Tools

| Tool | Consistency | Use when |
| :--- | :--- | :--- |
| [`CrossAggregateRule[T]`](#1-crossaggregaterule-naming-the-invariant) 📏 | either | You want to **name and type** a cross-aggregate rule explicitly |
| [`DomainService`](#2-domainservice-orchestrating-the-check) 🎯 | strong | You need to **orchestrate** a read-then-write across two aggregates |
| [`@authority`](#3-authority-designating-the-owning-aggregate) 👑 | strong | One aggregate is the clear **owner** of the invariant |
| [`EventBus` / `InMemoryEventBus`](#4-eventbus-publishing-domain-events) 📡 | eventual | You want **decoupled** reactions within the same bounded context |
| [`CrossAggregateInvariantHandler`](domain-events.md#crossaggregateinvarianthandler--enforcing-rules-across-aggregates) 🔗 | eventual | A handler needs the **repo + UoW** of the other aggregate to react |
| [`ProcessManager`](#5-processmanager-long-running-workflows) 🔄 | eventual | You need to **coordinate** a multi-step workflow across aggregates |

---

## 1. `CrossAggregateRule` — Naming the Invariant

`CrossAggregateRule[T]` is the cross-aggregate counterpart to `Rule[T]`. Instead of receiving a single aggregate, it receives a **context object** that holds all the aggregates involved in the rule.

```python
from dataclasses import dataclass
from hike import CrossAggregateRule, RuleBrokenError, UuidAggregate, Field, ValueObject

# --- Domain objects ---

class RosterSize(ValueObject[int]): ...
class MaxRoster(ValueObject[int]): ...

class Team(UuidAggregate):
    roster_size: Field[RosterSize]
    max_roster: Field[MaxRoster]

class Player(UuidAggregate):
    pass

# --- Context dataclass holds every aggregate involved ---

@dataclass
class TeamPlayerContext:
    team: Team
    player: Player

# --- The rule ---

class RosterLimitRule(CrossAggregateRule[TeamPlayerContext]):
    """A team cannot exceed its maximum roster size."""

    def is_broken(self, context: TeamPlayerContext) -> bool:
        return (
            context.team.roster_size.value
            >= context.team.max_roster.value
        )
```

Checking the rule:

```python
team = Team(roster_size=RosterSize(22), max_roster=MaxRoster(22))
new_player = Player()

ctx = TeamPlayerContext(team=team, player=new_player)
RosterLimitRule().check(ctx)   # raises RuleBrokenError — roster is already full
```

`check()` raises `RuleBrokenError` when broken, just like `raise_on_broken_rule()` on a single-aggregate `Rule`. The `broken_rule` attribute on the error gives you back the rule instance for logging or user messaging.

> **Why a context dataclass?**  
> It gives every argument a name and a type. Compare `rule.check(team, player)` (what order?) to `rule.check(TeamPlayerContext(team=team, player=player))` — the second is self-documenting and Pyright can verify the types.

> **`CrossAggregateRule` is never auto-checked.**  
> `@command` can only re-check invariants on *its own* aggregate. Cross-aggregate rules must be checked **explicitly**, usually inside a `DomainService`.

---

## 2. `DomainService` — Orchestrating the Check

A **domain service** is a class that coordinates multiple repositories and a `UnitOfWork` to enforce a cross-aggregate invariant. It doesn't hold domain state — it's a pure orchestrator.

```python
from hike import DomainService, IRepository, UnitOfWork, RuleBrokenError

class TeamRosterService(DomainService):
    """Enforces the roster-size invariant when adding a player to a team."""

    def __init__(
        self,
        team_repo: IRepository,
        player_repo: IRepository,
        uow: UnitOfWork,
    ) -> None:
        self._teams = team_repo
        self._players = player_repo
        self._uow = uow

    def add_player(self, team_id: object, player: Player) -> None:
        # 1. Load the authority aggregate
        team = self._teams.get_one(team_id)

        # 2. Check the cross-aggregate invariant
        ctx = TeamPlayerContext(team=team, player=player)
        RosterLimitRule().check(ctx)   # raises if broken

        # 3. Update both aggregates in one transaction
        with self._uow(self._teams, self._players):
            team.roster_size = team.roster_size + 1
            self._players.save(player)
            self._teams.update(team)
            self._uow.commit()
```

**When to use `DomainService`:**

- The rule requires checking a condition across two aggregates *before* writing.
- You want **strong consistency** (the violation is caught before any write commits).
- The coordination logic involves more than one repository and needs a clear home.

**When *not* to use `DomainService`:**

- The invariant only involves one aggregate — use `__invariants__` directly.
- You need to handle failure across a long multi-step flow — use a `ProcessManager` instead.

---

## 3. `@authority` — Designating the Owning Aggregate

When one aggregate is the clear owner of a cross-aggregate invariant, decorate its authority method with `@authority`. This is a documentation marker — it signals to other developers which aggregate to load first when enforcing the rule.

```python
from hike import UuidAggregate, Field, ValueObject, authority

class Team(UuidAggregate):
    roster_size: Field[RosterSize]
    max_roster: Field[MaxRoster]

    @authority
    def can_add_player(self) -> bool:
        """Authority check: does this team have room for another player?"""
        return self.roster_size.value < self.max_roster.value
```

The application layer respects the authority by loading `Team` first and delegating to it:

```python
def add_player(team_id, player, team_repo, player_repo, uow):
    team = team_repo.get_one(team_id)   # load the authority first

    if not team.can_add_player():       # delegate to authority
        raise RuleBrokenError(RosterLimitRule())

    with uow(player_repo):
        player_repo.save(player)
        uow.commit()
```

`@authority` does not change behaviour at runtime — it sets `method._is_authority_check = True` as a searchable marker. The value comes from the convention it enforces: anyone reading the code knows *Team* owns this decision, not the caller.

> **Tip:** Use `@authority` + a plain method when the rule is simple and fits naturally on one aggregate. Use `CrossAggregateRule` + `DomainService` when the rule involves state from multiple aggregates or needs to be tested in isolation.

---

## 4. `EventBus` — Publishing Domain Events

`EventBus` provides a **publish/subscribe interface** for domain events. Aggregate commands raise events; repositories collect them automatically. The `UnitOfWork` dispatches them on commit via one of two modes:

- **`bus=`** — in-memory synchronous dispatch, same process.
- **`outbox=`** — events written to DB atomically, relayed to another bounded context via outbox/inbox.

```python
# In-memory sync
with uow(repo, bus=bus):
    order.place()
    repo.save(order)
    uow.commit()  # → bus dispatches OrderPlaced, then commit (handler error = rollback)

# Outbox (cross-service, crash-safe)
with uow(repo, outbox=outbox_repo):
    order.place()
    repo.save(order)
    uow.commit()  # → order + outbox row committed atomically
```

For the full explanation — why, when, and step-by-step setup — see the **[Domain Events](domain-events.md)** guide.

---

## 5. `ProcessManager` — Long-Running Workflows

A `ProcessManager` listens for domain events and coordinates a **multi-step workflow** across aggregates. Each step is a separate transaction; the process manager reacts to the outcome of one step to trigger the next.

### Example: refund workflow

A refund involves two aggregates — `Order` and `Payment`. When an `OrderRefundRequested` event fires, the process manager orchestrates both:

```python
from dataclasses import dataclass
from hike import DomainEvent, ProcessManager, EventBus

@dataclass(frozen=True)
class OrderRefundRequested(DomainEvent):
    order_id: object
    payment_id: object
    amount: float

class RefundManager(ProcessManager):
    def __init__(
        self,
        bus: EventBus,
        order_repo: IRepository,
        payment_repo: IRepository,
    ) -> None:
        self._orders = order_repo
        self._payments = payment_repo
        super().__init__(bus)   # calls _subscribe(); must come after setting repos

    def _subscribe(self) -> None:
        self._bus.subscribe(OrderRefundRequested, self._on_refund_requested)

    def _on_refund_requested(self, event: OrderRefundRequested) -> None:
        order = self._orders.get_one(event.order_id)
        payment = self._payments.get_one(event.payment_id)

        # Each aggregate enforces its own invariants
        order.mark_refunded()
        payment.refund(event.amount)

        with uow(self._orders, self._payments):
            self._orders.update(order)
            self._payments.update(payment)
            uow.commit()
```

The `ProcessManager` base class calls `_subscribe()` during `__init__`. That's why subclasses must set their own state (`self._orders`, `self._payments`) **before** calling `super().__init__(bus)` — `_subscribe()` may need them.

### Stateless vs. stateful process managers

`ProcessManager` holds no domain state by default. If your workflow spans many steps and needs to remember where it left off (e.g., a three-stage approval chain), store that state in a dedicated **saga aggregate** — a regular `UuidAggregate` whose fields track the workflow's progress. The process manager reads and updates this aggregate at each step.

### When to use `ProcessManager`

| Use it when… | Don't use it when… |
| :--- | :--- |
| A workflow spans multiple events and multiple aggregates | The whole flow fits in a single `DomainService` method |
| Steps may fail independently and need compensation logic | Strong consistency is required at every step |
| You want to decouple the trigger (e.g., `OrderPlaced`) from the reaction (e.g., `PaymentInitiated`) | The rule is a simple pre-condition check |

---

## Choosing the Right Pattern

Here is a decision tree to guide your choice:

```
Does the invariant involve more than one aggregate?
└─ No → Use __invariants__ + @rule on the single aggregate.

└─ Yes:
   Can a brief violation be tolerated (e.g., milliseconds while events propagate)?
   ├─ No (must be atomic):
   │   Is one aggregate clearly the "owner" of the rule?
   │   ├─ Yes → @authority + delegate to it from the application layer.
   │   └─ No  → CrossAggregateRule + DomainService.
   │
   └─ Yes (eventual consistency is acceptable):
       Is the reaction a single step?
       ├─ Yes → EventBus subscription + plain handler.
       └─ No  → ProcessManager (multi-step, possibly with a saga aggregate).
```

---

## Quick Reference

```python
from hike import (
    CrossAggregateRule,              # name and type a cross-aggregate invariant
    DomainService,                   # orchestrate the check + save
    authority,                       # mark which aggregate owns the invariant
    EventBus,                        # publish/subscribe interface
    EventHandler,                    # abstract handler object base class
    InMemoryEventBus,                # synchronous in-memory bus (great for tests)
    CrossAggregateInvariantHandler,  # handler with repo+uow for the other aggregate
    ProcessManager,                  # coordinate a multi-step workflow
    RuleBrokenError,                 # raised when any rule (single or cross-aggregate) fires
)
```

For event serialization, outbox, and inbox imports see the **[Domain Events](domain-events.md)** quick reference.

---

## Recommended External Reading

- [Effective Aggregate Design Part II — Making Aggregates Work Together](https://kalele.io/wp-content/uploads/2019/01/DDD_COMMUNITY_ESSAY_AGGREGATES_PART_2.pdf) — Vaughn Vernon's canonical guide.
- [Saga and Process Manager — Event-Driven.io](https://event-driven.io/en/saga_process_manager_distributed_transactions/) — Practical comparison of the two patterns.
- [Set-Based Validation in Event Sourcing](https://medium.com/@arsalan.valoojerdi/cross-aggregate-validations-exploring-set-based-validation-techniques-in-event-sourcing-e28d9e0ffce6) — Deep dive on uniqueness checks at scale.