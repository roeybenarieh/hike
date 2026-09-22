from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, NoReturn
from uuid import UUID

from hike.events.interfaces.background_task import IBackgroundTasks, Task
from hike.persistence.repository import IRepository, ResourceDoesNotExistError
from hike.persistence.uow import UnitOfWork
from hike.specifications import FieldByName

from .data import SagaTimeout
from .manager import SagaManager


class TimeoutManager(IBackgroundTasks):
    """Delivers expired :class:`SagaTimeout` records to registered saga managers.

    Implements :class:`IBackgroundTasks` — pass it to your lifecycle manager
    alongside broker subscribers::

        tm = TimeoutManager(timeout_repo, poll_interval=1.0, uow=UnitOfWork(timeout_ctx))
        tm.register(shipping_manager)
        tm.register(order_manager)
        # hand tm.tasks() to your thread pool / runner
    """

    def __init__(
        self,
        timeout_repo: IRepository[UUID, SagaTimeout, Any],
        poll_interval: float = 1.0,
        uow: UnitOfWork[Any] | None = None,
    ) -> None:
        self._timeout_repo = timeout_repo
        self._poll_interval = poll_interval
        self._uow = uow
        self._managers: dict[str, SagaManager[Any]] = {}
        self._running = True

    def register(self, manager: SagaManager[Any]) -> None:
        """Register *manager* so it receives expired timeouts for its saga type.

        The manager's saga class name is used as the routing key.  Register all
        saga managers before starting the background task::

            tm = TimeoutManager(timeout_repo)
            tm.register(order_manager)
            tm.register(shipping_manager)
        """
        self._managers[manager.saga_type.__name__] = manager

    def tasks(self) -> list[Task]:
        return [self.start]

    def cleanup(self) -> None:
        """Signal the polling loop to stop after its current sleep expires."""
        self._running = False

    def start(self) -> NoReturn:
        """Poll the timeout repository in a tight loop and dispatch expired timeouts.

        Returned by :meth:`tasks` so that a task runner can execute it in a
        background thread.  After delivering each batch of expired timeouts, the
        method sleeps until the next scheduled timeout (capped at
        ``poll_interval``) to avoid busy-waiting when no timeouts are due.
        Stop the loop by calling :meth:`cleanup` from another thread.
        """
        while self._running:
            if self._uow is not None:
                with self._uow(self._timeout_repo, auto_commit=True):
                    sleep_secs = self._dispatch_expired()
            else:
                sleep_secs = self._dispatch_expired()
            time.sleep(sleep_secs)
        raise RuntimeError("TimeoutManager stopped")

    def _dispatch_expired(self) -> float:
        now = datetime.now(timezone.utc)
        expired: list[SagaTimeout] = self._timeout_repo.get_many(  # type: ignore[assignment]
            FieldByName("fire_at") <= now
        )
        for timeout in expired:
            manager = self._managers.get(timeout.saga_type_name)
            if manager is not None:
                manager.handle_timeout(timeout)
            try:
                self._timeout_repo.delete(timeout)
            except ResourceDoesNotExistError:
                pass  # already deleted by another TM or by test cleanup

        future: list[SagaTimeout] = self._timeout_repo.get_many(  # type: ignore[assignment]
            FieldByName("fire_at") > now
        )
        if future:
            soonest = min(t.fire_at for t in future)
            return max(0.0, min(
                (soonest - datetime.now(timezone.utc)).total_seconds(),
                self._poll_interval,
            ))
        return self._poll_interval
