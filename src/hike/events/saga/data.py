from __future__ import annotations

import base64
import dataclasses
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Self
from uuid import UUID, uuid4

from hike.persistence.persistable import DataclassPersistable


@dataclass(eq=False)
class SagaData(DataclassPersistable[UUID]):
    """Mutable state bag for a saga instance, persisted between handler calls.

    ``SagaManager`` creates a fresh instance when a ``@started_by`` handler
    fires with no existing saga, and reloads it from the repository for every
    subsequent handler call.  All fields must have defaults so that
    ``SagaManager`` can call ``YourData()`` without arguments.

    The built-in ``completed`` and ``completed_steps`` fields are managed by
    the framework — do not set them directly.  Call ``Saga.mark_as_complete()``
    to finish a saga and ``Saga.compensate()`` to roll back completed steps.

    Always decorate subclasses with ``@dataclass(eq=False)`` to preserve
    identity-based equality inherited from ``Persistable``.  Without it the
    dataclass-generated ``__eq__`` compares fields and sets ``__hash__ = None``::

        @dataclass(eq=False)
        class OrderFulfillmentData(SagaData):
            order_id: str = ""
            is_payment_confirmed: bool = False
            is_shipment_dispatched: bool = False
    """

    id: UUID = field(default_factory=uuid4)
    completed: bool = field(default=False, init=False)
    completed_steps: list[str] = field(default_factory=lambda: [], init=False)

    def get_id(self) -> UUID:
        return self.id


@dataclass(eq=False)
class SagaTimeout(DataclassPersistable[UUID]):
    """A persisted record of a future timeout, created by ``SagaContext.request_timeout``.

    ``TimeoutManager`` polls the repository for records whose ``fire_at`` has
    passed and delivers each one to the matching ``SagaManager``, which calls
    the saga's ``@timeout_handler`` with ``state`` as the argument.

    This class is framework-internal — users never instantiate it directly.
    Interact with timeouts through ``SagaContext.request_timeout`` and the
    ``@timeout_handler`` decorator instead::

        # scheduling (inside a saga handler):
        ctx.request_timeout(PaymentDeadline(order_id=self.data.order_id), timedelta(hours=24))

        # handling (on the saga class):
        @timeout_handler
        def on_payment_deadline(self, state: PaymentDeadline, ctx: SagaContext) -> None:
            ctx.publish(CancelOrder(order_id=state.order_id))
            self.mark_as_complete()
    """

    saga_id: UUID
    saga_type_name: str
    state: Any
    fire_at: datetime
    id: UUID = field(default_factory=uuid4)

    def get_id(self) -> UUID:
        return self.id

    def to_dict(self) -> dict[str, Any]:
        d = {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}
        # Pickle-encode state so any Python object survives JSON / BSON round-trips.
        d["state"] = base64.b64encode(pickle.dumps(self.state)).decode("ascii")
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        d = dict(data)
        raw = d.get("state")
        if isinstance(raw, (str, bytes)):
            payload = raw.encode("ascii") if isinstance(raw, str) else raw
            d["state"] = pickle.loads(base64.b64decode(payload))
        # Some backends (e.g. PyMongo) return offset-naive UTC datetimes.
        # Normalise to UTC-aware so arithmetic with datetime.now(timezone.utc) works.
        fire_at = d.get("fire_at")
        if isinstance(fire_at, datetime) and fire_at.tzinfo is None:
            d["fire_at"] = fire_at.replace(tzinfo=timezone.utc)
        return super().from_dict(d)
