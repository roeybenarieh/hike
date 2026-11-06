"""Retry policies built on Tenacity.  Requires ``pip install ihike[resilience]``."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import tenacity as t

from hike.resilience.circuit_breaker import CircuitOpenError
from hike.resilience.policy import RetryPolicy


class _TenacityPolicy:
    """Adapts a Tenacity ``Retrying`` to :class:`RetryPolicy`.

    ``Retrying`` keeps per-call state, so every call runs on a fresh copy.
    """

    def __init__(self, retrying: Any) -> None:
        self._retrying = retrying

    def __call__[**P, R](
        self, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs
    ) -> R:
        result: R = self._retrying.copy()(fn, *args, **kwargs)
        return result


def _build(
    attempts: int,
    retry_on: tuple[type[Exception], ...],
    wait: Any,
    sleep: Callable[[float], None],
) -> RetryPolicy:
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    # CircuitOpenError must never be retried, even if retry_on matches it.
    retry = t.retry_if_exception_type(retry_on) & t.retry_if_not_exception_type(
        CircuitOpenError
    )
    return _TenacityPolicy(
        t.Retrying(
            stop=t.stop_after_attempt(attempts),
            wait=wait,
            retry=retry,
            sleep=sleep,
            reraise=True,
        )
    )


def fixed_delay(
    delay: float,
    *,
    attempts: int,
    retry_on: tuple[type[Exception], ...],
    sleep: Callable[[float], None] = time.sleep,
) -> RetryPolicy:
    """Waits exactly *delay* seconds between attempts."""
    return _build(attempts, retry_on, t.wait_fixed(delay), sleep)


def fixed_delay_with_jitter(
    delay: float,
    *,
    attempts: int,
    retry_on: tuple[type[Exception], ...],
    sleep: Callable[[float], None] = time.sleep,
) -> RetryPolicy:
    """Waits a random time in ``[0, delay]`` seconds between attempts."""
    return _build(attempts, retry_on, t.wait_random(0, delay), sleep)


def exponential_backoff_with_jitter(
    *,
    attempts: int,
    retry_on: tuple[type[Exception], ...],
    initial_delay: float = 0.1,
    multiplier: float = 2.0,
    max_delay: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
) -> RetryPolicy:
    """Random wait in ``[0, min(max_delay, initial_delay * multiplier ** (n - 1))]``, full jitter."""
    wait = t.wait_random_exponential(
        multiplier=initial_delay, max=max_delay, exp_base=multiplier
    )
    return _build(attempts, retry_on, wait, sleep)
