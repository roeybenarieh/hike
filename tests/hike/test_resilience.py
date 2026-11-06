"""Unit tests for hike.resilience (retry templates, CircuitBreaker). Time is injected."""
from __future__ import annotations

import subprocess
import sys
import threading

import pytest

from hike.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    NoRetry,
    RetryPolicy,
    exponential_backoff_with_jitter,
    fixed_delay,
    fixed_delay_with_jitter,
)


class _Boom(Exception):
    pass


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Flaky:
    """Fails *failures* times, then returns 'ok'."""

    def __init__(self, failures: int, exc: Exception | None = None) -> None:
        self.calls = 0
        self._failures = failures
        self._exc = exc or _Boom("transient")

    def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self._failures:
            raise self._exc
        return "ok"


def _fixed(attempts: int) -> RetryPolicy:
    return fixed_delay(0.0, attempts=attempts, retry_on=(_Boom,), sleep=lambda _: None)


# ---------------------------------------------------------------------------
# Retry templates
# ---------------------------------------------------------------------------


def test_returns_result_and_passes_arguments_through() -> None:
    def add(a: int, b: int = 0) -> int:
        return a + b

    assert _fixed(attempts=3)(add, 1, b=2) == 3


def test_recovers_after_transient_failures() -> None:
    fn = _Flaky(failures=2)
    assert _fixed(attempts=3)(fn) == "ok"
    assert fn.calls == 3


def test_reraises_the_last_exception_unchanged_when_attempts_are_exhausted() -> None:
    err = _Boom("permanent")
    fn = _Flaky(failures=99, exc=err)
    with pytest.raises(_Boom) as caught:
        _fixed(attempts=3)(fn)
    assert caught.value is err
    assert fn.calls == 3


def test_exception_outside_retry_on_propagates_immediately() -> None:
    fn = _Flaky(failures=99, exc=ValueError("bug"))
    with pytest.raises(ValueError):
        _fixed(attempts=5)(fn)
    assert fn.calls == 1


def test_circuit_open_error_is_never_retried_even_if_retry_on_matches_it() -> None:
    retrying = fixed_delay(0.0, attempts=5, retry_on=(Exception,), sleep=lambda _: None)
    fn = _Flaky(failures=99, exc=CircuitOpenError("c", 1.0))
    with pytest.raises(CircuitOpenError):
        retrying(fn)
    assert fn.calls == 1


def test_fixed_delay_sleeps_the_same_amount_between_attempts() -> None:
    sleeps: list[float] = []
    with pytest.raises(_Boom):
        fixed_delay(0.5, attempts=4, retry_on=(_Boom,), sleep=sleeps.append)(_Flaky(failures=99))
    assert sleeps == [0.5, 0.5, 0.5]


def test_fixed_delay_with_jitter_stays_within_zero_and_delay() -> None:
    sleeps: list[float] = []
    with pytest.raises(_Boom):
        fixed_delay_with_jitter(0.5, attempts=20, retry_on=(_Boom,), sleep=sleeps.append)(_Flaky(failures=99))
    assert len(sleeps) == 19
    assert all(0.0 <= s <= 0.5 for s in sleeps)


def test_exponential_backoff_window_grows_and_is_capped() -> None:
    sleeps: list[float] = []
    with pytest.raises(_Boom):
        exponential_backoff_with_jitter(
            attempts=6, retry_on=(_Boom,), initial_delay=0.1, multiplier=2.0, max_delay=0.5, sleep=sleeps.append,
        )(_Flaky(failures=99))
    windows = [min(0.5, 0.1 * 2 ** n) for n in range(5)]  # 0.1, 0.2, 0.4, 0.5, 0.5
    assert len(sleeps) == len(windows)
    assert all(0.0 <= s <= w for s, w in zip(sleeps, windows))


def test_no_retry_runs_once() -> None:
    fn = _Flaky(failures=99)
    with pytest.raises(_Boom):
        NoRetry()(fn)
    assert fn.calls == 1


@pytest.mark.parametrize("attempts", [0, -1])
def test_invalid_attempts_are_rejected(attempts: int) -> None:
    with pytest.raises(ValueError):
        _fixed(attempts=attempts)


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


def _breaker(clock: _Clock | None = None, **kw: int | float) -> CircuitBreaker:
    return CircuitBreaker(
        name="t", failure_threshold=3, recovery_timeout=10.0, record_on=(_Boom,),
        clock=clock or _Clock(), **kw,  # pyright: ignore[reportArgumentType]
    )


def _fail(breaker: CircuitBreaker, times: int) -> None:
    for _ in range(times):
        with pytest.raises(_Boom):
            breaker.call(_Flaky(failures=99))


def test_opens_after_threshold_consecutive_failures_and_rejects_without_calling() -> None:
    breaker = _breaker()
    _fail(breaker, 3)
    assert breaker.state is CircuitState.OPEN

    fn = _Flaky(failures=0)
    with pytest.raises(CircuitOpenError) as caught:
        breaker.call(fn)
    assert fn.calls == 0
    assert 0 < caught.value.retry_after <= 10.0


def test_a_success_resets_the_consecutive_failure_count() -> None:
    breaker = _breaker()
    _fail(breaker, 2)
    assert breaker.call(_Flaky(failures=0)) == "ok"
    _fail(breaker, 2)
    assert breaker.state is CircuitState.CLOSED


def test_exception_not_in_record_on_counts_as_a_response_not_a_failure() -> None:
    breaker = _breaker()
    _fail(breaker, 2)
    with pytest.raises(ValueError):
        breaker.call(_Flaky(failures=99, exc=ValueError("business rule")))
    _fail(breaker, 2)
    assert breaker.state is CircuitState.CLOSED


def test_stays_open_until_the_recovery_timeout_then_probe_success_closes() -> None:
    clock = _Clock()
    breaker = _breaker(clock)
    _fail(breaker, 3)

    clock.now += 9.9
    with pytest.raises(CircuitOpenError):
        breaker.call(_Flaky(failures=0))

    clock.now += 0.2
    assert breaker.call(_Flaky(failures=0)) == "ok"
    assert breaker.state is CircuitState.CLOSED
    assert breaker.call(_Flaky(failures=0)) == "ok"


def test_failed_probe_reopens_for_a_full_timeout() -> None:
    clock = _Clock()
    breaker = _breaker(clock)
    _fail(breaker, 3)

    clock.now += 10
    _fail(breaker, 1)  # the probe
    assert breaker.state is CircuitState.OPEN

    clock.now += 9
    with pytest.raises(CircuitOpenError):
        breaker.call(_Flaky(failures=0))


def test_only_one_probe_runs_at_a_time() -> None:
    clock = _Clock()
    breaker = _breaker(clock)
    _fail(breaker, 3)
    clock.now += 10

    probe_running = threading.Event()
    release = threading.Event()
    results: list[str] = []

    def slow_probe() -> str:
        probe_running.set()
        assert release.wait(timeout=5)
        return "probe"

    t = threading.Thread(target=lambda: results.append(breaker.call(slow_probe)))
    t.start()
    assert probe_running.wait(timeout=5)

    fn = _Flaky(failures=0)
    with pytest.raises(CircuitOpenError):
        breaker.call(fn)
    assert fn.calls == 0

    release.set()
    t.join(timeout=5)
    assert results == ["probe"]
    assert breaker.state is CircuitState.CLOSED


def test_interrupted_probe_frees_the_slot_without_deciding_the_outcome() -> None:
    clock = _Clock()
    breaker = _breaker(clock)
    _fail(breaker, 3)
    clock.now += 10

    def interrupted() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        breaker.call(interrupted)
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.call(_Flaky(failures=0)) == "ok"  # a new probe is allowed
    assert breaker.state is CircuitState.CLOSED


def test_completion_from_before_the_circuit_opened_is_ignored() -> None:
    breaker = _breaker()

    def slow_success_that_started_while_closed() -> str:
        _fail(breaker, 3)  # meanwhile the circuit opens
        return "late"

    assert breaker.call(slow_success_that_started_while_closed) == "late"
    assert breaker.state is CircuitState.OPEN


@pytest.mark.parametrize(
    "kwargs", [{"failure_threshold": 0}, {"recovery_timeout": -1.0}],
)
def test_invalid_breaker_configuration_is_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        CircuitBreaker(**kwargs)  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def test_retry_around_breaker_stops_as_soon_as_the_circuit_opens() -> None:
    breaker = CircuitBreaker(failure_threshold=2, record_on=(_Boom,), clock=_Clock())
    retrying = _fixed(attempts=10)
    fn = _Flaky(failures=99)

    with pytest.raises(CircuitOpenError):
        retrying(lambda: breaker.call(fn))

    assert fn.calls == 2  # not 10: the open circuit ended the retry loop


# ---------------------------------------------------------------------------
# Required resilience extra
# ---------------------------------------------------------------------------


def test_importing_resilience_without_tenacity_raises_install_hint() -> None:
    # Subprocess: the package is already imported in this process.
    code = "import sys; sys.modules['tenacity'] = None; import hike.resilience"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "ihike[resilience]" in result.stderr
