from __future__ import annotations

import base64
import dataclasses
import time as _time
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from cloudevents.v1.conversion import to_dict as _sdk_to_dict
from cloudevents.v1.http import CloudEvent as _CloudEvent

from hike.domain_event import Event
from hike.events.integration_event import IntegrationEvent

# Valid CloudEvents extension attribute value types (spec §3.3).
type CloudEventsExtensionValue = bool | int | str | bytes | datetime


def _ext_attr_to_str(val: CloudEventsExtensionValue) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, bytes):
        return base64.b64encode(val).decode()
    return str(val)


# Field names on the Event base that should not appear in the CloudEvents data payload.
_BASE_FIELD_NAMES: frozenset[str] = frozenset(
    f.name for f in dataclasses.fields(Event)  # type: ignore[arg-type]
)

# CloudEvents attribute names that must not bleed into the domain data dict.
_CLOUDEVENTS_ATTRS: frozenset[str] = frozenset({
    "specversion", "id", "source", "type", "time",
    "dataschema", "subject", "datacontenttype",
})


class CloudEventSchema:
    """Patches an :class:`~hike.events.integration_event.IntegrationEvent` subclass
    to serialize/deserialize as CloudEvents 1.0 structured-mode dicts.

    Call :meth:`apply` to install CloudEvents-formatted ``to_dict`` /
    ``from_dict`` methods directly on the event class::

        CloudEventSchema("//orders-service").apply(OrderPlaced)

        ev = OrderPlaced(order_id="42")
        wire = ev.to_dict()               # CloudEvents 1.0 structured dict
        ev2 = OrderPlaced.from_dict(wire) # restored from wire dict

    :meth:`apply` returns the class unchanged, so it can also be used as a
    decorator (apply the schema *after* ``@dataclass``)::

        @CloudEventSchema("//orders-service").apply
        @dataclass(frozen=True, kw_only=True, eq=False)
        class OrderPlaced(IntegrationEvent):
            order_id: str
    """

    def __init__(self, source: str) -> None:
        if not source:
            raise ValueError("source must be a non-empty URI-reference")
        self._source = source
        self._dataschema: str | None = None
        self._subject: str | None = None
        self._extensions: dict[str, CloudEventsExtensionValue] = {}

    def with_dataschema(self, v: str) -> CloudEventSchema:
        self._dataschema = v
        return self

    def with_subject(self, v: str) -> CloudEventSchema:
        self._subject = v
        return self

    def with_extension(self, key: str, val: CloudEventsExtensionValue) -> CloudEventSchema:
        """Add a custom CloudEvents extension attribute."""
        self._extensions[key] = val
        return self

    def apply[TEvent: IntegrationEvent](self, event_cls: type[TEvent]) -> type[TEvent]:
        """Patch ``to_dict`` and ``from_dict`` on *event_cls* in-place.

        After this call:

        - ``instance.to_dict()`` returns a CloudEvents 1.0 structured-mode dict
          with the configured attributes and domain fields under ``data``.
        - ``EventClass.from_dict(wire)`` reconstructs an instance from such a dict.

        Returns *event_cls* so this method can be used as a class decorator.
        """
        schema = self

        def _to_dict(self_: TEvent) -> dict[str, Any]:
            payload: dict[str, Any] = {
                f.name: getattr(self_, f.name)
                for f in dataclasses.fields(event_cls)  # type: ignore[arg-type]
                if f.name not in _BASE_FIELD_NAMES
            }
            # occurred_at is annotated datetime | None but may carry a float timestamp.
            occ_raw: Any = self_.occurred_at
            if isinstance(occ_raw, datetime):
                occ_ts: float = occ_raw.timestamp()
            elif occ_raw is not None:
                occ_ts = float(occ_raw)
            else:
                occ_ts = _time.time()
            attrs: dict[str, str] = {
                "specversion": "1.0",
                "id": str(self_.id),
                "source": schema._source,
                "type": type(self_).event_type(),
                "time": datetime.fromtimestamp(occ_ts, tz=UTC).isoformat(),
            }
            for attr_name, val in [
                ("dataschema", schema._dataschema),
                ("subject", schema._subject),
            ]:
                if val is not None:
                    attrs[attr_name] = str(val)
            for ext_key, ext_val in schema._extensions.items():
                attrs[ext_key] = _ext_attr_to_str(ext_val)
            return dict(_sdk_to_dict(_CloudEvent(attrs, payload)))

        def _from_dict(cls_: type[TEvent], data: dict[str, Any]) -> TEvent:
            raw_ce_data: Any = data.get("data") or {}
            if not isinstance(raw_ce_data, dict):
                raise ValueError(
                    f"CloudEvents 'data' must be a mapping, got {type(raw_ce_data).__name__!r}"
                )
            ce_data: dict[str, Any] = cast(dict[str, Any], raw_ce_data)
            id_ = UUID(str(data["id"]))
            time_raw: str | None = data.get("time")
            occurred_at: float = (
                datetime.fromisoformat(time_raw).timestamp()
                if time_raw
                else _time.time()
            )
            domain_fields: dict[str, Any] = {
                k: v for k, v in ce_data.items()
                if k not in _CLOUDEVENTS_ATTRS
            }
            return cls_(id=id_, occurred_at=occurred_at, **domain_fields)  # type: ignore[return-value]

        event_cls.to_dict = _to_dict  # type: ignore[method-assign]
        event_cls.from_dict = classmethod(_from_dict)  # type: ignore[assignment]
        return event_cls
