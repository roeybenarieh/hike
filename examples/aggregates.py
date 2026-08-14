"""Aggregate examples.

Covers:
- Defining an aggregate with domain events
- @command (bare) — mutations without invariant checks
- @command(invariants=[...]) — rule() one-liners and Rule subclasses
- ReadOnlyView guard: Field[Entity] is read-only outside a @command
- RuleBrokenError raised when an invariant is violated
"""

from dataclasses import dataclass, field as dc_field

from hike import (
    DomainEvent,
    Field,
    Rule,
    RuleBrokenError,
    UuidAggregate,
    UuidEntity,
    ValueObject,
    command,
    field,
    non_empty,
    non_negative,
    rule,
)


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

class Money(ValueObject[float]):
    __validators__ = [non_negative]


class ItemName(ValueObject[str]):
    __validators__ = [non_empty]


# ---------------------------------------------------------------------------
# Child entity
# ---------------------------------------------------------------------------

class OrderItem(UuidEntity):
    name: Field[ItemName]
    price: Field[Money]


# ---------------------------------------------------------------------------
# Domain events
# ---------------------------------------------------------------------------

@dataclass
class ItemAdded(DomainEvent):
    item_id: object
    item_name: str


@dataclass
class OrderShipped(DomainEvent):
    order_id: object


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Rules — @rule decorator uses the function name as the message.
# Typed functions (not lambdas) let pyright resolve o as Order via inference.
# ---------------------------------------------------------------------------

@rule                                          # function name becomes the message
def order_has_items(o: "Order") -> bool:
    return len(o.items) == 0

@rule(message="Order total cannot exceed 10,000")  # explicit message overrides name
def order_total_within_cap(o: "Order") -> bool:
    return o.total.value > 10_000

@rule
def order_total_matches_items(o: "Order") -> bool:
    return abs(o.total.value - sum(i.price.value for i in o.items)) > 0.01


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

class Order(UuidAggregate):
    # __invariants__ are checked automatically after every __init__ call,
    # including subclass construction. Rules are collected from the full MRO.
    __invariants__ = [order_total_matches_items]

    total: Field[Money] = field(default_factory=lambda: Money(0))
    # init=False keeps these out of the constructor signature; dataclass
    # initialises them from their factories before __post_init__.
    items: list[OrderItem] = dc_field(default_factory=lambda: [], init=False)
    _shipped: bool = dc_field(default=False, init=False)

    @property
    def shipped(self) -> bool:
        return self._shipped

    @command
    def add_item(self, item: OrderItem) -> None:
        self.items.append(item)
        self.total = Money(self.total.value + item.price.value)
        self._events.append(ItemAdded(item.id, item.name.value))

    # order_has_items only makes sense at ship-time, not at creation time,
    # so it stays in @command(invariants=[...]) rather than __invariants__.
    @command(invariants=[order_has_items, order_total_within_cap])
    def ship(self) -> None:
        self._shipped = True
        self._events.append(OrderShipped(self.id))

    # invariants='all' re-runs every rule in __invariants__ from the full MRO.
    @command(invariants='all')
    def apply_discount(self, percent: float) -> None:
        factor = 1 - percent / 100
        self.total = Money(self.total.value * factor)
        for item in self.items:
            item.price = Money(item.price.value * factor)


# ---------------------------------------------------------------------------
# Entity __invariants__ + ReadOnlyView guard
# ---------------------------------------------------------------------------

class Speed(ValueObject[int]): ...


@rule(message="Speed cannot be negative")
def speed_non_negative(e: "Engine") -> bool:
    return e.speed.value < 0


@rule(message="Car max speed cannot exceed 300")
def car_speed_within_limit(c: "Car") -> bool:
    return c.engine.speed.value > 300


class Engine(UuidEntity):
    # __invariants__ works on plain entities too, not only aggregates.
    __invariants__ = [speed_non_negative]

    speed: Field[Speed]

    @command(invariants=[speed_non_negative])
    def set_speed(self, new_speed: Speed) -> None:
        self.speed = new_speed


class Car(UuidAggregate):
    # Car adds its own invariant on top of Engine's.
    # Both are checked at construction via the full MRO traversal.
    __invariants__ = [car_speed_within_limit]

    engine: Field[Engine]

    @command
    def tune(self, new_speed: Speed) -> None:
        self.engine.speed = new_speed  # inside @command → real entity, write OK


# ---------------------------------------------------------------------------
# Reusable named Rule via subclassing (alternative to rule())
# ---------------------------------------------------------------------------

class TotalMatchesItems(Rule[Order]):
    """Total must equal the sum of all item prices."""

    def is_broken(self, obj: Order) -> bool:
        expected = sum(item.price.value for item in obj.items)
        return abs(obj.total.value - expected) > 0.01


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def main() -> None:
    order = Order()

    # --- happy path: add items ---
    chair = OrderItem(name=ItemName("Chair"), price=Money(250))
    table = OrderItem(name=ItemName("Table"), price=Money(750))
    order.add_item(chair)
    order.add_item(table)

    print(f"Total after items: {order.total.value}")   # 1000.0
    print(f"Events so far: {[type(e).__name__ for e in order.get_events()]}")

    # --- apply discount; invariant ensures total stays consistent with items ---
    order.apply_discount(10)
    print(f"Total after 10% discount: {order.total.value}")  # 900.0

    # --- ship ---
    order.ship()
    print(f"Shipped: {order.shipped}")
    print(f"All events: {[type(e).__name__ for e in order.get_events()]}")

    # --- invariant violation: ship an empty order ---
    empty_order = Order()
    try:
        empty_order.ship()
    except RuleBrokenError as e:
        print(f"\nCaught RuleBrokenError: {e.broken_rule!r}")

    # --- named Rule subclass used for explicit checks ---
    checker = TotalMatchesItems()
    fresh = Order()
    fresh.add_item(OrderItem(name=ItemName("Sofa"), price=Money(500)))
    print(f"\nTotal matches items: {not checker.is_broken(fresh)}")  # True

    # --- __invariants__ on entity: checked automatically after __init__ ---
    Engine(speed=Speed(100))                # OK

    try:
        Engine(speed=Speed(-1))             # speed_non_negative in Engine.__invariants__
    except RuleBrokenError as e:
        print(f"\nEngine init invariant: {e.broken_rule!r}")

    # --- __invariants__ on aggregate: full MRO traversal ---
    # Car.__invariants__ (car_speed_within_limit) is checked on top of any
    # inherited invariants from UuidAggregate / Aggregate / Entity.
    Car(engine=Engine(speed=Speed(100)))    # OK

    try:
        Car(engine=Engine(speed=Speed(400)))  # car_speed_within_limit in Car.__invariants__
    except RuleBrokenError as e:
        print(f"Car init invariant:    {e.broken_rule!r}")

    # --- @command(invariants=[...]) still works for per-command checks ---
    engine = Engine(speed=Speed(100))
    engine.set_speed(Speed(200))
    print(f"\nEngine speed after set_speed: {engine.speed.value}")  # 200

    try:
        engine.set_speed(Speed(-1))         # speed_non_negative in command invariants
    except RuleBrokenError as e:
        print(f"Command invariant:     {e.broken_rule!r}")

    # --- ReadOnlyView guard: Field[Entity] is read-only outside @command ---
    car = Car(engine=Engine(speed=Speed(0)))

    car.tune(Speed(120))                    # OK — mutation goes through @command
    print(f"\nEngine speed after tune: {car.engine.speed.value}")  # 120

    try:
        car.engine.speed = Speed(999)       # outside @command → ReadOnlyView blocks
    except AttributeError as e:
        print(f"Guard caught: {e}")


if __name__ == "__main__":
    main()
