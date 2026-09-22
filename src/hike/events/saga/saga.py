from __future__ import annotations

import inspect
import typing
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, ClassVar, cast, get_type_hints

from hike.domain_event import Event

from .context import SagaContext
from .data import SagaData
from .mapper import SagaMapper


class SagaRole(Enum):
    STARTED_BY = "started_by"
    HANDLES = "handles"
    TIMEOUT = "timeout"


# Unbound method types — the first argument is always the saga instance (self).
# SagaEventHandler uses Any for the event arg: concrete handlers narrow to a specific
# Event subclass, and Callable contravariance would reject them if bound to Event.
# __init_subclass__ enforces the Event subclass constraint at class-definition time.
type SagaEventHandler = Callable[[Any, Event, SagaContext], None]
type SagaTimeoutHandler = Callable[[Any, Any, SagaContext], None]
type SagaCompensator = Callable[[Any, SagaContext], None]


def _handler_msg_type(fn: Callable[..., Any]) -> type:
    """Return the message/state type from the second parameter of a saga handler."""
    hints = get_type_hints(fn)
    params = list(inspect.signature(fn).parameters.keys())
    if len(params) < 2:
        raise TypeError(
            f"Saga handler '{fn.__qualname__}' must have at least 2 parameters "
            f"(self, message_or_state)."
        )
    param_name = params[1]
    msg_type = hints.get(param_name)
    if msg_type is None:
        raise TypeError(
            f"Saga handler '{fn.__qualname__}' must annotate its second parameter "
            f"with the message or timeout-state type."
        )
    return msg_type  # type: ignore[return-value]


# ── Decorators ────────────────────────────────────────────────────────────────


def started_by(fn: SagaEventHandler) -> SagaEventHandler:
    """Decorate a saga method as the entry-point handler for a saga.

    When an incoming event matches this handler and no correlated saga instance
    is found in the repository, a new ``SagaData`` instance is created
    automatically.  When a matching instance already exists it is reused —
    multiple ``@started_by`` handlers can therefore act as alternative entry
    points for the same saga::

        @started_by
        def on_order_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
            self.data.order_id = msg.order_id

        @started_by
        def on_order_reserved(self, msg: OrderReserved, ctx: SagaContext) -> None:
            # also starts a fresh saga if none exists yet
            self.data.order_id = msg.order_id
    """
    fn._saga_role = SagaRole.STARTED_BY  # type: ignore[attr-defined]
    return fn


def handles(fn: SagaEventHandler) -> SagaEventHandler:
    """Decorate a saga method as a continuation handler.

    The saga instance **must** already exist in the repository — if no matching
    instance is found, ``SagaManager`` raises :exc:`SagaNotFoundError`.  Use
    this for events that can only arrive after a ``@started_by`` handler has
    already run::

        @handles
        def on_payment_confirmed(self, msg: PaymentConfirmed, ctx: SagaContext) -> None:
            self.data.is_paid = True
            if self.data.is_shipped:
                self.mark_as_complete()
    """
    fn._saga_role = SagaRole.HANDLES  # type: ignore[attr-defined]
    return fn


def timeout_handler(fn: SagaTimeoutHandler) -> SagaTimeoutHandler:
    """Decorate a saga method as a timeout handler.

    The method is called by ``TimeoutManager`` when a timeout previously
    scheduled with ``SagaContext.request_timeout`` fires.  The second parameter
    receives the user-defined state object that was passed to
    ``request_timeout`` — the type of that parameter determines which timeout
    this handler answers::

        @dataclass
        class PaymentDeadline:
            order_id: str

        @timeout_handler
        def on_payment_deadline(self, state: PaymentDeadline, ctx: SagaContext) -> None:
            ctx.publish(CancelOrder(order_id=state.order_id))
            self.mark_as_complete()
    """
    fn._saga_role = SagaRole.TIMEOUT  # type: ignore[attr-defined]
    return fn


def compensates(forward: SagaEventHandler) -> Callable[[SagaCompensator], SagaCompensator]:
    """Link a compensation method to a ``@started_by`` or ``@handles`` forward handler.

    When :meth:`Saga.compensate` is called the framework invokes each registered
    compensator in **reverse** order of the forward steps that have completed.

    The compensation method receives only ``self`` and ``ctx`` — not the original
    message (which is no longer available at compensation time).  Use ``self.data``
    to read any saga state set by the forward handler::

        @started_by
        def on_flight_reserved(self, msg: FlightReserved, ctx: SagaContext) -> None:
            self.data.flight_id = msg.flight_id

        @compensates(on_flight_reserved)
        def cancel_flight(self, ctx: SagaContext) -> None:
            ctx.publish(CancelFlight(flight_id=self.data.flight_id))

    The forward handler **must be defined before** its compensator in the class
    body. ``__init_subclass__`` validates at class-definition time that the
    referenced function is an actual ``@started_by`` or ``@handles`` handler.
    """

    def decorator(fn: SagaCompensator) -> SagaCompensator:
        fn._compensates = forward  # type: ignore[attr-defined]
        return fn

    return decorator


# ── Saga base class ───────────────────────────────────────────────────────────


class Saga[TSagaData: SagaData](ABC):
    """Abstract base for all saga process managers.

    Concrete subclasses:

    1. Inherit ``Saga[YourDataClass]``.
    2. Declare correlation in :meth:`configure_how_to_find_saga`.
    3. Tag handler methods with ``@started_by``, ``@handles``, or
       ``@timeout_handler`` — the message type is inferred from the
       second parameter annotation.
    4. Wire to an event subscriber via :class:`SagaManager`.

    Example::

        @dataclass(eq=False)
        class ShippingPolicyData(SagaData):
            order_id: str = ""
            is_order_billed: bool = False
            is_order_submitted: bool = False

        class ShippingPolicy(Saga[ShippingPolicyData]):

            def configure_how_to_find_saga(self, mapper):
                mapper.map_saga("order_id")\\
                    .to_message(OrderBilled, "order_id")\\
                    .to_message(OrderSubmitted, "order_id")\\
                    .to_message(OrderShipped, "order_id")

            @started_by
            def on_order_billed(self, msg: OrderBilled, ctx: SagaContext) -> None:
                self.data.is_order_billed = True

            @started_by
            def on_order_submitted(self, msg: OrderSubmitted, ctx: SagaContext) -> None:
                self.data.order_id = msg.order_id
                self.data.is_order_submitted = True

            @handles
            def on_order_shipped(self, msg: OrderShipped, ctx: SagaContext) -> None:
                self.mark_as_complete()
    """

    data: TSagaData
    """The saga's mutable state, persisted between handler invocations.

    Injected by ``SagaManager`` before every handler call. Read and write
    ``self.data`` inside handlers to carry information across events.
    """

    event_dispatch: ClassVar[dict[type[Event], tuple[SagaRole, SagaEventHandler]]]
    """Maps each handled ``Event`` subclass to its ``(SagaRole, handler)`` pair.

    Built from ``@started_by`` and ``@handles`` decorators at class-definition time.
    ``SagaManager`` consults this table to route an incoming event to the correct
    method and to decide whether it may create a new saga instance.
    """

    timeout_dispatch: ClassVar[dict[type[Any], SagaTimeoutHandler]]
    """Maps each timeout-state type to its ``@timeout_handler`` callable.

    Built from ``@timeout_handler`` decorators at class-definition time.
    ``TimeoutManager`` looks up the state type of a fired timeout here to find
    the method to invoke.
    """

    compensation_dispatch: ClassVar[dict[str, SagaCompensator]]
    """Maps each forward-handler name to its ``@compensates`` callable.

    Built from ``@compensates`` decorators at class-definition time.
    :meth:`compensate` iterates :attr:`SagaData.completed_steps` in reverse and
    looks up each step name here to find the method that undoes it.
    """

    data_class: ClassVar[type[SagaData]]
    """The concrete ``SagaData`` subclass bound to this saga.

    Extracted from the ``Saga[TSagaData]`` generic parameter at class-definition
    time.  ``SagaManager`` uses it to instantiate a fresh ``SagaData`` object
    when a ``@started_by`` handler receives its first event.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

        event_dispatch: dict[type[Event], tuple[SagaRole, SagaEventHandler]] = {}
        timeout_dispatch: dict[type, SagaTimeoutHandler] = {}
        compensation_dispatch: dict[str, SagaCompensator] = {}

        seen: set[str] = set()
        for klass in reversed(cls.__mro__):
            for attr_name, raw_val in vars(klass).items():
                if attr_name in seen:
                    continue
                seen.add(attr_name)

                # Saga handlers are always regular instance methods; skip descriptors.
                if not callable(raw_val) or isinstance(raw_val, (classmethod, staticmethod, property)):
                    continue
                fn: Callable[..., Any] = raw_val

                role: SagaRole | None = getattr(fn, "_saga_role", None)
                if role is None:
                    continue

                try:
                    msg_type = _handler_msg_type(fn)
                except TypeError as exc:
                    raise TypeError(
                        f"In saga '{cls.__name__}', handler '{attr_name}': {exc}"
                    ) from exc

                if role == SagaRole.TIMEOUT:
                    timeout_dispatch[msg_type] = fn
                else:
                    try:
                        is_event_subclass = issubclass(msg_type, Event)
                    except TypeError:
                        is_event_subclass = False
                    if not is_event_subclass:
                        raise TypeError(
                            f"@{role.value} handler '{attr_name}' on '{cls.__name__}' must "
                            f"annotate its message parameter with an Event subclass, "
                            f"got {msg_type!r}."
                        )
                    event_dispatch[msg_type] = (role, fn)

        # Second pass: build compensation_dispatch from @compensates decorators.
        seen_comp: set[str] = set()
        forward_names = {fn.__name__ for _role, fn in event_dispatch.values()}
        for klass in reversed(cls.__mro__):
            for attr_name, raw_val in vars(klass).items():
                if attr_name in seen_comp:
                    continue
                seen_comp.add(attr_name)
                if not callable(raw_val) or isinstance(raw_val, (classmethod, staticmethod, property)):
                    continue
                forward: Callable[..., Any] | None = getattr(raw_val, "_compensates", None)
                if forward is None:
                    continue
                if forward.__name__ not in forward_names:
                    raise TypeError(
                        f"@compensates on '{attr_name}' in '{cls.__name__}' references "
                        f"'{forward.__name__}', which is not a @started_by or @handles "
                        f"handler in this saga."
                    )
                compensation_dispatch[forward.__name__] = cast(SagaCompensator, raw_val)

        cls.event_dispatch = event_dispatch
        cls.timeout_dispatch = timeout_dispatch
        cls.compensation_dispatch = compensation_dispatch

        # Extract TSagaData from Saga[TSagaData] in __orig_bases__.
        for base in getattr(cls, "__orig_bases__", ()):
            origin = typing.get_origin(base)
            if origin is None:
                continue
            try:
                is_saga = issubclass(origin, Saga)
            except TypeError:
                continue
            if not is_saga:
                continue
            args = typing.get_args(base)
            if args:
                try:
                    if issubclass(args[0], SagaData):
                        cls.data_class = args[0]
                        break
                except TypeError:
                    pass

    @abstractmethod
    def configure_how_to_find_saga(self, mapper: SagaMapper[TSagaData]) -> None:
        """Declare how incoming message fields map to saga-data fields.

        Called once per :class:`SagaManager` at construction time — not
        per-message.  Use the fluent DSL::

            mapper.map_saga("order_id")\\
                .to_message(OrderPlaced, "order_id")\\
                .to_message(OrderBilled, "order_id")
        """

    def mark_as_complete(self) -> None:
        """Signal that this saga has finished.

        Sets ``self.data.completed = True``.  After the handler returns,
        ``SagaManager`` deletes the saga data from the repository and calls the
        ``on_complete`` callback (if configured).  Call this as the last
        statement in the final handler::

            @handles
            def on_order_shipped(self, msg: OrderShipped, ctx: SagaContext) -> None:
                ctx.publish(OrderFulfilled(order_id=self.data.order_id))
                self.mark_as_complete()
        """
        self.data.completed = True

    def compensate(self, ctx: SagaContext) -> None:
        """Run all registered compensations in LIFO order.

        Iterates :attr:`SagaData.completed_steps` in reverse and calls the
        ``@compensates``-linked method for each step that has one registered.
        Steps with no registered compensator are silently skipped.

        Call this inside a ``@handles`` failure-event handler::

            @handles
            def on_payment_failed(self, msg: PaymentFailed, ctx: SagaContext) -> None:
                self.compensate(ctx)     # undoes earlier steps in reverse
                self.mark_as_complete()
        """
        for step_name in reversed(self.data.completed_steps):
            method = type(self).compensation_dispatch.get(step_name)
            if method is not None:
                method(self, ctx)
