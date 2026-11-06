"""Retry and circuit-breaker policies.

Requires the ``resilience`` extra: ``pip install ihike[resilience]``.
Importing this package without Tenacity installed raises :class:`ImportError`.

    retry = exponential_backoff_with_jitter(attempts=4, retry_on=(ConnectionError, TimeoutError))
    breaker = CircuitBreaker(name="billing-api", failure_threshold=5, recovery_timeout=30)

    # retry outside, breaker inside: every attempt is observed by the breaker,
    # and an open circuit stops the retry loop immediately.
    invoice = retry(breaker.call, client.fetch_invoice, invoice_id)

Create one :class:`CircuitBreaker` per dependency and share it between callers.
Retried operations must be idempotent.
"""

try:
    import tenacity  # noqa: F401  # pyright: ignore[reportUnusedImport, reportMissingImports]
except ImportError as _exc:
    raise ImportError(
        "The resilience extra is required for hike.resilience. "
        "Install it with: pip install ihike[resilience]"
    ) from _exc

from hike.resilience.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState
from hike.resilience.policy import NoRetry, RetryPolicy
from hike.resilience.tenacity_policies import (
    exponential_backoff_with_jitter,
    fixed_delay,
    fixed_delay_with_jitter,
)

__all__ = [
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "NoRetry",
    "RetryPolicy",
    "exponential_backoff_with_jitter",
    "fixed_delay",
    "fixed_delay_with_jitter",
]
