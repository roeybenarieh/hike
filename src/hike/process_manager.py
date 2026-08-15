from abc import ABC, abstractmethod

from .domain_event import EventBus


class ProcessManager(ABC):
    """Coordinates a long-running workflow across aggregates via domain events.

    Subclasses subscribe to events in ``_subscribe`` and react in handler
    methods.  No mutable workflow state is held here — if you need to
    persist state across multiple steps, store it in a dedicated aggregate.

    Example::

        class RefundManager(ProcessManager):
            def __init__(
                self,
                bus: EventBus,
                order_repo: IRepository[UUID, Order],
                payment_repo: IRepository[UUID, Payment],
            ) -> None:
                self._orders = order_repo
                self._payments = payment_repo
                super().__init__(bus)   # calls _subscribe()

            def _subscribe(self) -> None:
                self._bus.subscribe(OrderRefundRequested, self._on_refund_requested)

            def _on_refund_requested(self, event: OrderRefundRequested) -> None:
                ...
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._subscribe()

    @abstractmethod
    def _subscribe(self) -> None:
        """Register all event subscriptions.  Called once at construction."""
