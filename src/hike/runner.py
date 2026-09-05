from __future__ import annotations

import multiprocessing
import signal
import threading
from typing import Any

from hike.events.interfaces.background_task import IBackgroundTasks


def _sigterm_to_exit(*_: Any) -> None:
    raise SystemExit(0)


def _wrap(provider: IBackgroundTasks, task: Any) -> Any:
    """Return *task* wrapped so *provider.cleanup* runs in the same worker on exit.

    A SIGTERM handler is installed so that when the process is terminated via
    :meth:`ProcessBackgroundTaskRunner.stop`, the ``finally`` block still fires
    and cleanup runs inside the child.  SIGKILL (last resort) cannot be caught.
    """

    def wrapped() -> None:
        signal.signal(signal.SIGTERM, _sigterm_to_exit)
        try:
            task()
        finally:
            provider.cleanup()

    return wrapped


class ThreadedBackgroundTaskRunner:
    """Runs tasks as daemon threads; stops them via stored cleanups then joins.

    Args:
        revive: When ``True``, a task that exits for any reason is
            automatically restarted until :meth:`stop` is called.
    """

    def __init__(self, *, revive: bool = False) -> None:
        self._threads: list[threading.Thread] = []
        self._cleanups: list[IBackgroundTasks] = []
        self._revive = revive
        self._stop_event = threading.Event()

    def start(self, *providers: IBackgroundTasks) -> None:
        for provider in providers:
            self._cleanups.append(provider)
            for task in provider.tasks():
                target = self._reviving(task) if self._revive else task
                t = threading.Thread(target=target, daemon=True)
                t.start()
                self._threads.append(t)

    def _reviving(self, task: Any) -> Any:
        def wrapper() -> None:
            while not self._stop_event.is_set():
                try:
                    task()
                except Exception:
                    pass

        return wrapper

    def stop(self) -> None:
        self._stop_event.set()
        for provider in self._cleanups:
            try:
                provider.cleanup()
            except Exception:
                pass
        for t in self._threads:
            t.join(timeout=5.0)


class ProcessBackgroundTaskRunner:
    """Runs tasks as daemon processes; stops them with SIGTERM then SIGKILL.

    Uses the ``fork`` start method on Linux, meaning each task callable is
    executed in a child process that inherits the parent's address space.
    Each task is wrapped so that the provider's
    :meth:`~IBackgroundTasks.cleanup` runs *inside* the child process — both
    in a ``finally`` block on normal/exception exit and via a ``SIGTERM``
    handler on :meth:`stop` — so it can safely manage connections created
    after the fork.

    Args:
        revive: When ``True``, a process that exits for any reason is
            automatically restarted by a supervisor thread until
            :meth:`stop` is called.

    .. caution::
        Not all clients are fork-safe.  Pass task callables that create their
        own connections after the fork, rather than reusing connections
        inherited from the parent.  redis-py is fork-safe (it auto-reconnects
        on PID change); pymongo, pika, and confluent-kafka are not.
    """

    def __init__(self, *, revive: bool = False) -> None:
        # ForkProcess (returned by get_context("fork").Process) is a subtype of
        # BaseProcess but pyright's stubs treat it as incompatible with
        # multiprocessing.Process, so we type the list as Any.
        self._processes: list[Any] = []
        self._revive = revive
        self._stop_event = threading.Event()
        self._supervisor_threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def start(self, *providers: IBackgroundTasks) -> None:
        ctx = multiprocessing.get_context("fork")
        for provider in providers:
            for task in provider.tasks():
                target = _wrap(provider, task)
                p = ctx.Process(target=target, daemon=True)
                p.start()
                with self._lock:
                    slot = len(self._processes)
                    self._processes.append(p)
                if self._revive:
                    sup = threading.Thread(
                        target=self._supervise, args=(target, slot), daemon=True
                    )
                    sup.start()
                    self._supervisor_threads.append(sup)

    def _supervise(self, task: Any, slot: int) -> None:
        while True:
            with self._lock:
                if self._stop_event.is_set():
                    break
                current = self._processes[slot]
            current.join()
            with self._lock:
                if self._stop_event.is_set():
                    break
                ctx = multiprocessing.get_context("fork")
                p = ctx.Process(target=task, daemon=True)
                p.start()
                self._processes[slot] = p

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            processes = list(self._processes)
        for p in processes:
            if p.is_alive():
                p.terminate()
        for p in processes:
            p.join(timeout=5.0)
            if p.is_alive():
                p.kill()
                p.join(timeout=1.0)
        for sup in self._supervisor_threads:
            sup.join(timeout=2.0)
