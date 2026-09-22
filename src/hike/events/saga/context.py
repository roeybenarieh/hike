from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID

from hike.events.integration_event import IntegrationEvent
from hike.persistence.repository import IRepository

from .data import SagaTimeout


class SagaContext:
    """Injected into every saga handler — the saga's interface to the outside world.

    Provides three capabilities:

    * **Publishing** — buffer integration events to be dispatched after the
      handler persists successfully (``ctx.publish(event)``).
    * **Timeouts** — schedule a future callback into the saga
      (``ctx.request_timeout(state, delay)``).
    * **Locking** — hold an exclusive repository lock for the saga's lifetime
      (``ctx.lock(repo, id)``).

    Usage example::

        @started_by
        def on_order_placed(self, msg: OrderPlaced, ctx: SagaContext) -> None:
            self.data.order_id = msg.order_id
            ctx.publish(OrderAccepted(order_id=msg.order_id))
            ctx.request_timeout(PaymentDeadline(msg.order_id), timedelta(hours=24))
    """

    def __init__(
        self,
        saga_id: UUID,
        saga_type_name: str,
        # TODO: what is an event buffer and should I implement it?
        event_buffer: list[IntegrationEvent],
        timeout_repo: IRepository[UUID, SagaTimeout, Any],
        register_lock: Callable[[IRepository[Any, Any, Any], Any], None] | None = None,
    ) -> None:
        self._saga_id = saga_id
        self._saga_type_name = saga_type_name
        self._event_buffer = event_buffer
        self._timeout_repo = timeout_repo
        self._register_lock = register_lock

    @property
    def saga_id(self) -> UUID:
        """The unique id of the current saga instance."""
        return self._saga_id

    def lock(
        self,
        repo: IRepository[Any, Any, Any],
        id: Any,
        *,
        timeout: float | None = None,
    ) -> None:
        """Acquire an exclusive lock on aggregate *id* in *repo*.

        The lock is held for the lifetime of the saga and is automatically
        released when the handler fails or the saga completes.  Acquiring a
        lock already held by this saga is a no-op (reentrant).

        The TTL (dead-man's switch that auto-expires the lock if the process
        crashes before the saga finishes) is configured on the repository
        constructor, not here.  Persistent providers set it to 300 s by default.

        :param repo: The repository that owns *id*.
        :param id: The aggregate id to lock.
        :param timeout: Maximum seconds to wait. ``None`` means wait forever.
        :raises LockConflictError: if the lock cannot be acquired within *timeout*.
        """
        repo.acquire_lock(id, owner=self._saga_id, timeout=timeout)
        if self._register_lock is not None:
            self._register_lock(repo, id)

    def publish(self, event: IntegrationEvent) -> None:
        """Buffer an integration event for dispatch after the handler persists successfully.

        Events are held in memory and flushed by ``SagaManager`` only after
        the saga data is saved or updated.  If the handler raises, the buffer
        is discarded and no events are published.  Requires ``publisher=`` on
        ``SagaManager``; if not set, ``SagaManager`` raises ``RuntimeError``
        when the buffer is non-empty.
        """
        self._event_buffer.append(event)

    def request_timeout(self, state: object, delay: timedelta) -> None:
        """Schedule a timeout callback to fire after *delay*.

        Persists a :class:`SagaTimeout` record immediately.  When the timeout
        fires, ``TimeoutManager`` calls the saga's ``@timeout_handler`` whose
        second parameter type matches ``type(state)``, passing *state* as the
        argument.  Requires ``timeout_repo=`` on :class:`SagaManager`::

            ctx.request_timeout(PaymentDeadline(order_id=self.data.order_id), timedelta(hours=24))
        """
        self._timeout_repo.save(
            SagaTimeout(
                saga_id=self._saga_id,
                saga_type_name=self._saga_type_name,
                state=state,
                fire_at=datetime.now(timezone.utc) + delay,
            )
        )
