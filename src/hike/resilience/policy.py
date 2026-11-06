"""Retry policy protocol and the built-in no-retry policy.

Real policies live in :mod:`hike.resilience.tenacity_policies`.  The package requires
the ``resilience`` extra (``pip install ihike[resilience]``).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class RetryPolicy(Protocol):
    """Runs *fn* and retries it according to the policy.

    Implementations must keep per-call state local, so one policy instance can be
    shared by concurrent callers.
    """

    def __call__[**P, R](self, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R: ...


class NoRetry:
    """Calls *fn* exactly once.  The default wherever a policy is optional."""

    def __call__[**P, R](self, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
        return fn(*args, **kwargs)
