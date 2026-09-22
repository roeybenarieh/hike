from __future__ import annotations

from contextlib import ExitStack
from typing import Any, Callable, cast
from uuid import UUID

from hike.domain_event import Event
from hike.events.integration_event import IntegrationEvent
from hike.events.interfaces.background_task import IBackgroundTasks, Task
from hike.events.interfaces.publisher import IEventPublisher
from hike.events.interfaces.subscriber import IEventSubscriber
from hike.persistence.repository import IRepository, ResourceDoesNotExistError
from hike.persistence.uow import UnitOfWork
from hike.specifications import FieldByName
from .context import SagaContext
from .data import SagaData, SagaTimeout
from .errors import SagaNotFoundError
from .mapper import SagaMapper
from .saga import Saga, SagaEventHandler, SagaRole, SagaTimeoutHandler


class SagaManager[TSagaData: SagaData](IBackgroundTasks):
    """Routes integration events (and timeouts) to the correct saga instance.

    Usage with UnitOfWork (recommended)::

        saga_ctx = InMemoryDBContext()
        timeout_ctx = InMemoryDBContext()
        saga_repo = InMemoryPersistableRepository[UUID, ShippingPolicyData]()
        timeout_repo = InMemoryPersistableRepository[UUID, SagaTimeout]()

        manager = SagaManager(
            ShippingPolicy, saga_repo,
            timeout_repo=timeout_repo,
            publisher=bus,
            uow=UnitOfWork(saga_ctx),
            timeout_uow=UnitOfWork(timeout_ctx),
        )
        manager.subscribe_to(subscriber)

    Pass ``on_complete`` to be notified when a saga instance finishes.  The
    callback receives the completed saga's UUID and is called from the same
    context that called :meth:`handle` or :meth:`handle_timeout` — no
    threading primitives are introduced by ``SagaManager`` itself::

        event = threading.Event()
        manager = SagaManager(ShippingPolicy, repo, on_complete=lambda _: event.set())
    """

    def __init__(
            self,
            saga_type: type[Saga[TSagaData]],
            repository: IRepository[UUID, TSagaData, Any],
            *,
            publisher: IEventPublisher[Any],
            timeout_repo: IRepository[UUID, SagaTimeout, Any],
            uow: UnitOfWork[Any] | None = None,
            timeout_uow: UnitOfWork[Any] | None = None,
            on_complete: Callable[[UUID], None] | None = None,
    ):
        self.saga_type = saga_type
        self._repo = repository
        self._publisher = publisher
        self._timeout_repo = timeout_repo
        self._uow = uow
        self._timeout_uow = timeout_uow
        self._on_complete = on_complete
        self._saga_locks: dict[UUID, list[tuple[IRepository[Any, Any, Any], Any]]] = {}

        dummy: Saga[TSagaData] = saga_type.__new__(saga_type)
        mapper: SagaMapper[TSagaData] = SagaMapper()
        dummy.configure_how_to_find_saga(mapper)
        self._correlation_map: dict[type[Event], tuple[str, str]] = mapper.rules

    def handle(self, event: Event) -> None:
        """Dispatch *event* to the appropriate saga handler.

        Finds (or creates) the correlated saga instance, injects its ``data``,
        calls the handler, then saves or deletes the saga data.

        Do **not** pass a ``SagaManager`` to ``subscriber.subscribe()`` — it
        registers under the base ``Event`` type and the saga never fires.
        Use :meth:`subscribe_to` instead.

        :raises TypeError: if the saga has no handler registered for this event type.
        :raises SagaNotFoundError: if a ``@handles`` method has no existing saga.
        """
        buffer: list[IntegrationEvent] = []
        with ExitStack() as stack:
            if self._uow is not None:
                stack.enter_context(self._uow(self._repo, auto_commit=True))
            if self._timeout_uow is not None:
                stack.enter_context(self._timeout_uow(self._timeout_repo, auto_commit=True))
            self._handle_impl(event, buffer)
        if buffer:
            self._publisher.publish(buffer)

    def _handle_impl(self, event: Event, buffer: list[IntegrationEvent]) -> None:
        event_type = type(event)
        dispatch = self.saga_type.event_dispatch.get(event_type)
        if dispatch is None:
            raise TypeError(
                f"Saga '{self.saga_type.__name__}' has no handler for "
                f"'{event_type.__name__}'. "
                f"Registered types: "
                f"{[t.__name__ for t in self.saga_type.event_dispatch]}."
            )

        method: SagaEventHandler
        role, method = dispatch
        correlation = self._correlation_map.get(event_type)

        existing: TSagaData | None = None
        if correlation is not None:
            saga_field, msg_field = correlation
            correlation_value = getattr(event, msg_field)
            results = self._repo.get_many(FieldByName(saga_field) == correlation_value)
            if results:
                existing = results[0]

        is_new: bool
        saga_data: TSagaData
        if existing is None:
            if role == SagaRole.HANDLES:
                raise SagaNotFoundError(event_type, event)  # type: ignore[arg-type]
            saga_data = cast(TSagaData, self.saga_type.data_class())
            is_new = True
        else:
            saga_data = existing
            is_new = False

        saga: Saga[TSagaData] = self.saga_type.__new__(self.saga_type)
        saga.data = saga_data

        saga_id = saga_data.id
        ctx = SagaContext(
            saga_id=saga_id,
            saga_type_name=self.saga_type.__name__,
            event_buffer=buffer,
            timeout_repo=self._timeout_repo,
            register_lock=lambda repo, lock_id: self._register_lock(saga_id, repo, lock_id),
        )

        try:
            method(saga, event, ctx)
        except Exception:
            self._release_saga_locks(saga_id)
            raise

        # Track completed step before persisting so it is stored atomically.
        if not saga_data.completed:
            saga_data.completed_steps.append(method.__name__)

        if saga_data.completed:
            if not is_new:
                self._repo.delete(saga_data)
            self._release_saga_locks(saga_data.id)
            self._signal_completion(saga_data.id)
        elif is_new:
            self._repo.save(saga_data)
        else:
            self._repo.update(saga_data)

    def handle_timeout(self, timeout: SagaTimeout) -> None:
        """Deliver an expired :class:`SagaTimeout` to the appropriate saga handler.

        Called exclusively by :class:`TimeoutManager` — do not call directly.
        Silently no-ops when the saga has already completed and been deleted, or
        when no ``@timeout_handler`` is registered for ``type(timeout.state)``.
        On success, saves (or deletes) the saga data and flushes buffered events
        the same way :meth:`handle` does.
        """
        buffer: list[IntegrationEvent] = []
        if self._uow is not None:
            with self._uow(self._repo, auto_commit=True):
                self._handle_timeout_impl(timeout, buffer)
        else:
            self._handle_timeout_impl(timeout, buffer)
        if buffer:
            self._publisher.publish(buffer)

    def _handle_timeout_impl(self, timeout: SagaTimeout, buffer: list[IntegrationEvent]) -> None:
        timeout_type: type[Any] = cast(type[Any], type(timeout.state))
        method: SagaTimeoutHandler | None = self.saga_type.timeout_dispatch.get(timeout_type)
        if method is None:
            return

        try:
            saga_data = self._repo.get_one(timeout.saga_id)
        except ResourceDoesNotExistError:
            return  # saga already completed and deleted

        if saga_data.completed:
            return

        saga: Saga[TSagaData] = self.saga_type.__new__(self.saga_type)
        saga.data = saga_data

        saga_id = saga_data.id
        ctx = SagaContext(
            saga_id=saga_id,
            saga_type_name=self.saga_type.__name__,
            event_buffer=buffer,
            timeout_repo=self._timeout_repo,
            register_lock=lambda repo, lock_id: self._register_lock(saga_id, repo, lock_id),
        )

        try:
            method(saga, timeout.state, ctx)
        except Exception:
            self._release_saga_locks(saga_id)
            raise

        if saga_data.completed:
            self._repo.delete(saga_data)
            self._release_saga_locks(saga_data.id)
            self._signal_completion(saga_data.id)
        else:
            self._repo.update(saga_data)

    def _register_lock(
            self,
            saga_id: UUID,
            repo: IRepository[Any, Any, Any],
            lock_id: Any,
    ) -> None:
        self._saga_locks.setdefault(saga_id, []).append((repo, lock_id))

    def _release_saga_locks(self, saga_id: UUID) -> None:
        locks = self._saga_locks.pop(saga_id, [])
        for repo, lock_id in locks:
            repo.release_lock(lock_id, owner=saga_id)

    def tasks(self) -> list[Task]:
        """Return background tasks from the configured publisher, or ``[]`` if none is set.

        Implements :class:`IBackgroundTasks` so that a ``SagaManager`` can be
        handed directly to a task runner alongside other subscribers.
        """
        publisher = self._publisher
        if not isinstance(publisher, IBackgroundTasks):
            return []
        return publisher.tasks()

    def cleanup(self) -> None:
        publisher = self._publisher
        if isinstance(publisher, IBackgroundTasks):
            publisher.cleanup()

    def inject_subscribers_to(self, subscriber: IEventSubscriber[Any]) -> None:
        """Subscribe this manager to every event type it handles on *subscriber*.

        Equivalent to calling ``subscriber.subscribe(EventType, self)`` for
        each event the saga's ``@started_by`` and ``@handles`` methods handle.
        Use this instead of wiring subscriptions manually::

            manager.subscribe_to(bus)
        """
        for event_cls in self.saga_type.event_dispatch:
            subscriber._subscribe(event_cls.event_type(), event_cls, self)  # type: ignore[arg-type]

    def _signal_completion(self, saga_id: UUID) -> None:
        if self._on_complete is not None:
            self._on_complete(saga_id)
