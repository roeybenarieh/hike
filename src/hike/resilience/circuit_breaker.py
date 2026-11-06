"""Consecutive-failure circuit breaker.  Has no third-party dependencies."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from enum import Enum


class CircuitOpenError(Exception):
    """The call was rejected without being executed because the circuit is not accepting calls."""

    def __init__(self, name: str, retry_after: float) -> None:
        super().__init__(f"circuit {name!r} is open; retry in {retry_after:.2f}s")
        self.name = name
        self.retry_after = retry_after


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _Outcome(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    ABANDONED = "abandoned"  # interrupted (KeyboardInterrupt, ...): says nothing about the dependency


class CircuitBreaker:
    """Consecutive-failure circuit breaker.

    * **closed** – calls run; *failure_threshold* consecutive failures open the circuit.
    * **open** – calls raise :class:`CircuitOpenError` without running, for *recovery_timeout* seconds.
    * **half-open** – after the timeout a single probe call is allowed; success closes the
      circuit, failure re-opens it.  Concurrent calls are rejected while the probe runs.

    Only exceptions in *record_on* count as failures.  Other exceptions mean the dependency
    answered (e.g. a business error) and count as successes.  Exceptions that are not
    ``Exception`` subclasses (``KeyboardInterrupt``...) are never recorded.

    State changes are guarded by a lock that is never held while the protected call runs.
    Completions that belong to an earlier circuit state (e.g. a slow call that started before
    the circuit opened) are ignored.  Like Polly, the breaker is passive: transitions happen
    on calls, not on a timer.
    """

    def __init__(
            self,
            *,
            name: str = "circuit",
            failure_threshold: int = 5,
            recovery_timeout: float = 30.0,
            record_on: tuple[type[Exception], ...] = (Exception,),
            clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if recovery_timeout < 0:
            raise ValueError("recovery_timeout must be >= 0")
        self.name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._record_on = record_on
        self._clock = clock
        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probing = False
        self._generation = 0

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    def call[**P, R](self, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
        generation = self._acquire()
        try:
            result = fn(*args, **kwargs)
        except self._record_on:
            self._release(generation, _Outcome.FAILURE)
            raise
        except Exception:
            self._release(generation, _Outcome.SUCCESS)
            raise
        except BaseException:
            self._release(generation, _Outcome.ABANDONED)
            raise
        self._release(generation, _Outcome.SUCCESS)
        return result

    def _acquire(self) -> int:
        with self._lock:
            if self._state is CircuitState.OPEN:
                remaining = self._recovery_timeout - (self._clock() - self._opened_at)
                if remaining > 0:
                    raise CircuitOpenError(self.name, remaining)
                self._transition(CircuitState.HALF_OPEN)
            if self._state is CircuitState.HALF_OPEN:
                if self._probing:
                    raise CircuitOpenError(self.name, 0.0)
                self._probing = True
            return self._generation

    def _release(self, generation: int, outcome: _Outcome) -> None:
        with self._lock:
            if generation != self._generation:
                return  # stale: this call began in a previous state
            if self._state is CircuitState.HALF_OPEN:
                self._probing = False
                if outcome is _Outcome.SUCCESS:
                    self._transition(CircuitState.CLOSED)
                elif outcome is _Outcome.FAILURE:
                    self._open()
            elif outcome is _Outcome.SUCCESS:
                self._failures = 0
            elif outcome is _Outcome.FAILURE:
                self._failures += 1
                if self._failures >= self._failure_threshold:
                    self._open()

    def _open(self) -> None:
        self._opened_at = self._clock()
        self._transition(CircuitState.OPEN)

    def _transition(self, state: CircuitState) -> None:
        self._state = state
        self._failures = 0
        self._probing = False
        self._generation += 1
