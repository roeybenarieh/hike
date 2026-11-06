# Resilience: Retry & Circuit Breaker

## What is resilience, and why it matters

Services depend on other services: databases, brokers, payment APIs. Those dependencies
fail in ways that are usually temporary: a dropped connection, a timeout, a broker restart.
Without protection, one such blip fails the whole operation, and a dependency that is
down can be hammered by retries until it never recovers.

Resilience is the set of techniques that keep a system working through these failures:

- **Retry** repeats an operation that failed with a transient error, with a growing,
  randomised wait so that many clients do not retry in lockstep.
- **Circuit breaker** stops calling a dependency that keeps failing, so callers fail fast
  and the dependency gets room to recover.

`hike.resilience` provides both for calls to unreliable dependencies. Nothing is applied
implicitly: you choose where a policy goes.

## Installation

```bash
pip install ihike[resilience]
```

## What is retry, and when to use it

Retry runs a failed operation again, in the hope that the failure was temporary. It helps
when the error is **transient**: a network hiccup, a timeout, a briefly unavailable
service, a lock that was released a moment later.

Retry does not help when the error is **permanent**: invalid input, a missing record, a
failed business rule, or a dependency that is down for hours. Repeating those calls only
adds load and delays the error. So retry only the exception types you know are transient
(`retry_on`), and let everything else propagate at once.

Also make sure the operation is **idempotent** before retrying it. If a timeout happens
after the first attempt already took effect, the retry must not apply it twice.

The simplest retry calls the operation again a fixed number of times when a known
transient error occurs:

```python
from hike.resilience import fixed_delay

policy = fixed_delay(0.5, attempts=3, retry_on=(ConnectionError,))
policy(client.fetch_invoice, invoice_id)  # up to 3 calls, 0.5s apart
```

## Retry policies

There are multiple ways to retry. Pick the one that fits the dependency:

| Policy | Behaviour |
|---|---|
| `NoRetry()` | Calls once. The default. |
| `fixed_delay(delay, *, attempts, retry_on)` | Waits exactly `delay` seconds between attempts. |
| `fixed_delay_with_jitter(delay, *, attempts, retry_on)` | Waits a random time in `[0, delay]`. |
| `exponential_backoff_with_jitter(*, attempts, retry_on, initial_delay=0.1, multiplier=2.0, max_delay=5.0)` | Random wait up to an exponentially growing window, capped at `max_delay` (full jitter). |

Every policy is callable as `policy(fn, *args, **kwargs)` and returns the result of `fn`.

- `attempts` is the total number of calls, including the first. Must be at least 1.
- `retry_on` is the tuple of exception types treated as transient. Other exceptions propagate immediately.
- The last exception is re-raised once attempts are exhausted.
- Each call gets its own retry state, so one policy instance can be shared.

```python
from hike.resilience import exponential_backoff_with_jitter

policy = exponential_backoff_with_jitter(attempts=4, retry_on=(ConnectionError, TimeoutError))
policy(client.fetch_invoice, invoice_id)
```

## Retrying event handlers

`with_retry(handler, policy)` wraps an `IEventHandler` so that each `handle` call runs under the policy:

```python
from hike.events.retry import with_retry
from hike.resilience import fixed_delay

event_handler = FulfillOrderEventHandler()
policy = fixed_delay(1.0, attempts=3, retry_on=(IOError,))
retriable_event_handler = with_retry(event_handler, policy)

# use the handler as usual
bus.subscribe(retriable_event_handler)
```

- For an `IReversibleEventHandler`, the wrapper is also reversible: both `handle` and `compensate` are retried, so saga compensation still works.
- Subscribers and event buses have no retry parameter. Wrap handlers instead.
- A handler that still fails after its retries is handled as before(like a regular handler failure).
- Retried handlers **must be idempotent**. A timeout does not tell you whether the first attempt took effect.

## Circuit breaker

A circuit breaker is a wrapper around calls to a dependency. It watches the results of those
calls and, when the dependency keeps failing, stops sending calls to it for a while. The name
comes from an electrical breaker that cuts the current when a circuit overloads.

It exists because retrying a dependency that is down makes things worse: every caller keeps
sending requests, the dependency gets no room to recover, and each caller waits for a timeout.
A breaker fails those calls immediately instead.

It works in three states:

- **Closed** (normal): calls go through. Each failure is counted. A success resets the count.
  After `failure_threshold` consecutive failures, the breaker opens.
- **Open**: calls are rejected at once with `CircuitOpenError`, without touching the
  dependency. This lasts for `recovery_timeout` seconds.
- **Half-open**: after the timeout, one probe call is let through. If it succeeds, the breaker
  closes again. If it fails, the breaker opens again for another timeout.

Code example:

```python
from hike.resilience import CircuitBreaker, CircuitOpenError

breaker = CircuitBreaker(
    name="billing-api",
    failure_threshold=5,
    recovery_timeout=30,
    record_on=(ConnectionError, TimeoutError),
)

try:
    invoice = breaker.call(client.fetch_invoice, invoice_id)
except CircuitOpenError as exc:
    print(f"try again in {exc.retry_after:.1f}s")
```

Only exceptions in `record_on` (default `Exception`) count as failures. Other exceptions count as successes, because the dependency answered.

Create one breaker per dependency and share it between callers. A breaker built per call never trips.

### Combining retry and the breaker

Put the retry outside the breaker, so every attempt is seen by the breaker and an open circuit stops the retry loop immediately:

```python
policy = fixed_delay(0.5, attempts=5, retry_on=(ConnectionError,))
invoice = policy(breaker.call, client.fetch_invoice, invoice_id)
```

When a handler is guarded by a breaker, the event subscribers treat `CircuitOpenError` specially: the message is left unacknowledged and is not dead-lettered. The subscriber pauses for the breaker's `retry_after`, capped at a few seconds, so it does not spin on redelivery.

## Quick reference

```python
from hike.events.retry import with_retry
from hike.resilience import (
    CircuitBreaker,
    NoRetry,
    exponential_backoff_with_jitter,
    fixed_delay,
    fixed_delay_with_jitter,
)

bus.subscribe(with_retry(handler, fixed_delay_with_jitter(0.2, attempts=3, retry_on=(IOError,))))
```
