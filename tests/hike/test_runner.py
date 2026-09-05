from __future__ import annotations

import multiprocessing
import threading
import time
from collections.abc import Callable
from typing import Any, NoReturn

from hike.events.interfaces.background_task import IBackgroundTasks, Task
from hike.runner import ProcessBackgroundTaskRunner, ThreadedBackgroundTaskRunner


class _SimpleProvider(IBackgroundTasks):
    """Minimal IBackgroundTasks whose task and cleanup are provided at construction."""

    def __init__(self, task: Callable[[], Any], cleanup: Callable[[], None] | None = None) -> None:
        self._task = task
        self._cleanup = cleanup

    def tasks(self) -> list[Task]:
        return [self._task]  # type: ignore[list-item]

    def cleanup(self) -> None:
        if self._cleanup is not None:
            self._cleanup()


# ---------------------------------------------------------------------------
# ThreadedBackgroundTaskRunner
# ---------------------------------------------------------------------------


def test_thread_revive_restarts_task() -> None:
    counter = {"n": 0}
    lock = threading.Lock()

    def task() -> None:
        with lock:
            counter["n"] += 1

    runner = ThreadedBackgroundTaskRunner(revive=True)
    runner.start(_SimpleProvider(task))

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        with lock:
            if counter["n"] >= 3:
                break
        time.sleep(0.01)

    runner.stop()
    assert counter["n"] >= 3


def test_thread_revive_stop_prevents_further_restarts() -> None:
    counter = {"n": 0}
    lock = threading.Lock()

    def task() -> None:
        with lock:
            counter["n"] += 1

    runner = ThreadedBackgroundTaskRunner(revive=True)
    runner.start(_SimpleProvider(task))

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        with lock:
            if counter["n"] >= 2:
                break
        time.sleep(0.01)

    runner.stop()
    snapshot = counter["n"]
    time.sleep(0.1)
    assert counter["n"] == snapshot


def test_thread_no_revive_default_behaviour() -> None:
    counter = {"n": 0}
    lock = threading.Lock()

    def task() -> None:
        with lock:
            counter["n"] += 1

    runner = ThreadedBackgroundTaskRunner()
    runner.start(_SimpleProvider(task))
    time.sleep(0.1)
    runner.stop()
    assert counter["n"] == 1


def test_thread_cleanup_called_on_stop() -> None:
    """Cleanup stored from the provider is called by stop() to unblock the task."""
    cleaned = {"done": False}
    stop_event = threading.Event()

    def task() -> None:
        stop_event.wait()

    def cleanup() -> None:
        cleaned["done"] = True
        stop_event.set()

    runner = ThreadedBackgroundTaskRunner()
    runner.start(_SimpleProvider(task, cleanup))
    runner.stop()

    assert cleaned["done"]


# ---------------------------------------------------------------------------
# ProcessBackgroundTaskRunner
# ---------------------------------------------------------------------------


def _increment_and_exit(shared: Any) -> None:
    with shared.get_lock():
        shared.value += 1


def test_process_revive_restarts_task() -> None:
    shared = multiprocessing.Value("i", 0)

    runner = ProcessBackgroundTaskRunner(revive=True)
    runner.start(_SimpleProvider(lambda: _increment_and_exit(shared)))

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if shared.value >= 3:
            break
        time.sleep(0.05)

    runner.stop()
    assert shared.value >= 3


def test_process_revive_stop_terminates_and_prevents_restart() -> None:
    shared = multiprocessing.Value("i", 0)

    runner = ProcessBackgroundTaskRunner(revive=True)
    runner.start(_SimpleProvider(lambda: _increment_and_exit(shared)))

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if shared.value >= 2:
            break
        time.sleep(0.05)

    runner.stop()
    snapshot = shared.value
    time.sleep(0.2)
    assert shared.value == snapshot


def test_process_cleanup_runs_inside_child_on_stop() -> None:
    """cleanup() runs inside the child process when stop() sends SIGTERM."""
    cleanup_ran = multiprocessing.Value("i", 0)

    def task() -> None:
        while True:
            time.sleep(0.05)

    def cleanup() -> None:
        with cleanup_ran.get_lock():
            cleanup_ran.value += 1

    runner = ProcessBackgroundTaskRunner()
    runner.start(_SimpleProvider(task, cleanup))
    time.sleep(0.2)
    runner.stop()

    assert cleanup_ran.value == 1


def test_process_cleanup_runs_inside_child_on_natural_exit() -> None:
    """cleanup() runs inside the child process when the task exits on its own."""
    cleanup_ran = multiprocessing.Value("i", 0)

    def task() -> None:
        pass

    def cleanup() -> None:
        with cleanup_ran.get_lock():
            cleanup_ran.value += 1

    runner = ProcessBackgroundTaskRunner()
    runner.start(_SimpleProvider(task, cleanup))

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if cleanup_ran.value >= 1:
            break
        time.sleep(0.05)

    runner.stop()
    assert cleanup_ran.value >= 1
