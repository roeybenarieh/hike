from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .common import DomainObject


@dataclass(frozen=True, kw_only=True, eq=False)
class Event:
    """Base for all events.

    Each event carries an auto-generated ``id`` (defaults to a ``uuid4()``)
    that uniquely identifies the event occurrence.
    """

    id: Any = field(default_factory=uuid4)
    occurred_at: float = field(default_factory=time.time)

    @classmethod
    def event_type(cls) -> str:
        return cls.__name__


class DomainEvent(DomainObject, Event):
    """Base for all domain events.

    Subclasses are plain frozen dataclasses::

        @dataclass(frozen=True)
        class OrderPlaced(Event):
            order_id: str   # positional; id is keyword-only with a default
    """
