from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from hike.events.cloud_event import CloudEventSchema
from hike.events.integration_event import IntegrationEvent


@dataclass(frozen=True, kw_only=True, eq=False)
class _OrderPlaced(IntegrationEvent):
    order_id: str


# ---------------------------------------------------------------------------
# IntegrationEvent — semantic marker
# ---------------------------------------------------------------------------


class TestIntegrationEventIsMarker:
    def test_is_domain_event(self) -> None:
        from hike.domain_event import DomainEvent
        assert issubclass(IntegrationEvent, DomainEvent)

    def test_subclass_instantiates(self) -> None:
        ev = _OrderPlaced(order_id="1")
        assert ev.order_id == "1"

    def test_id_is_uuid_by_default(self) -> None:
        ev = _OrderPlaced(order_id="1")
        assert isinstance(ev.id, UUID)

    def test_get_id_returns_id(self) -> None:
        ev = _OrderPlaced(order_id="1")
        assert ev.get_id() is ev.id


# ---------------------------------------------------------------------------
# CloudEventSchema — validation
# ---------------------------------------------------------------------------


class TestCloudEventSchemaValidation:
    def test_empty_source_raises(self) -> None:
        with pytest.raises(ValueError, match="source"):
            CloudEventSchema("")

    def test_apply_returns_the_class(self) -> None:
        @dataclass(frozen=True, kw_only=True, eq=False)
        class _Ev(IntegrationEvent):
            x: str

        result = CloudEventSchema("//s").apply(_Ev)
        assert result is _Ev


# ---------------------------------------------------------------------------
# to_dict — wire format
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _patch_order_placed() -> None:
    CloudEventSchema("//orders-service").apply(_OrderPlaced)


class TestToDict:
    def _wire(self, **kwargs: object) -> dict[str, object]:
        ev = _OrderPlaced(order_id="42", **kwargs)  # type: ignore[arg-type]
        return ev.to_dict()

    def test_required_keys_present(self) -> None:
        d = self._wire()
        for key in ("specversion", "id", "source", "type", "time", "data"):
            assert key in d, f"missing key: {key}"

    def test_specversion_value(self) -> None:
        assert self._wire()["specversion"] == "1.0"

    def test_source_value(self) -> None:
        assert self._wire()["source"] == "//orders-service"

    def test_type_defaults_to_class_name(self) -> None:
        assert self._wire()["type"] == "_OrderPlaced"

    def test_id_is_string(self) -> None:
        assert isinstance(self._wire()["id"], str)

    def test_time_is_iso8601_string(self) -> None:
        t = self._wire()["time"]
        assert isinstance(t, str)
        datetime.fromisoformat(t)

    def test_time_derived_from_occurred_at(self) -> None:
        occurred = 1_700_000_000.0
        d = self._wire(occurred_at=occurred)
        expected = datetime.fromtimestamp(occurred, tz=UTC).isoformat()
        assert d["time"] == expected

    def test_data_contains_domain_fields(self) -> None:
        d = self._wire()
        data = d["data"]
        assert isinstance(data, dict)
        assert data["order_id"] == "42"

    def test_data_does_not_contain_base_fields(self) -> None:
        d = self._wire()
        data = d["data"]
        assert isinstance(data, dict)
        for base_field in ("id", "occurred_at"):
            assert base_field not in data, f"base field leaked into data: {base_field}"

    def test_no_optional_attrs_by_default(self) -> None:
        d = self._wire()
        for attr in ("dataschema", "subject", "traceparent", "tracestate",
                     "correlationid", "partitionkey", "expirytime", "extensions"):
            assert attr not in d, f"unexpected attr in wire dict: {attr}"


# ---------------------------------------------------------------------------
# CloudEventSchema optional attributes
# ---------------------------------------------------------------------------


class TestCloudEventSchemaOptionalAttrs:
    def _apply_and_wire(self, **schema_kwargs: object) -> dict[str, object]:
        @dataclass(frozen=True, kw_only=True, eq=False)
        class _Ev(IntegrationEvent):
            x: str

        schema = CloudEventSchema("//s")
        for method, arg in schema_kwargs.items():
            getattr(schema, method)(arg)
        schema.apply(_Ev)
        return _Ev(x="v").to_dict()

    def test_dataschema_in_wire_dict(self) -> None:
        @dataclass(frozen=True, kw_only=True, eq=False)
        class _Ev(IntegrationEvent):
            x: str

        CloudEventSchema("//s").with_dataschema("https://schema.example.com/ev.json").apply(_Ev)
        d = _Ev(x="v").to_dict()
        assert d["dataschema"] == "https://schema.example.com/ev.json"

    def test_subject_in_wire_dict(self) -> None:
        @dataclass(frozen=True, kw_only=True, eq=False)
        class _Ev(IntegrationEvent):
            x: str

        CloudEventSchema("//s").with_subject("order/1").apply(_Ev)
        d = _Ev(x="v").to_dict()
        assert d["subject"] == "order/1"

    def test_extension_merged_into_top_level(self) -> None:
        @dataclass(frozen=True, kw_only=True, eq=False)
        class _Ev(IntegrationEvent):
            x: str

        CloudEventSchema("//s").with_extension("customattr", "value").apply(_Ev)
        d = _Ev(x="v").to_dict()
        assert d["customattr"] == "value"
        assert "extensions" not in d

    def test_can_be_used_as_decorator(self) -> None:
        @CloudEventSchema("//s").apply
        @dataclass(frozen=True, kw_only=True, eq=False)
        class _Ev(IntegrationEvent):
            x: str

        d = _Ev(x="v").to_dict()
        assert d["source"] == "//s"

    def test_unset_attrs_omitted(self) -> None:
        @dataclass(frozen=True, kw_only=True, eq=False)
        class _Ev(IntegrationEvent):
            x: str

        CloudEventSchema("//s").apply(_Ev)
        d = _Ev(x="v").to_dict()
        for opt in ("dataschema", "subject"):
            assert opt not in d


# ---------------------------------------------------------------------------
# from_dict roundtrip
# ---------------------------------------------------------------------------


class TestFromDict:
    def test_roundtrip_preserves_domain_fields(self) -> None:
        ev = _OrderPlaced(order_id="42")
        wire = ev.to_dict()
        restored = _OrderPlaced.from_dict(wire)
        assert restored.order_id == "42"

    def test_roundtrip_preserves_id_as_uuid(self) -> None:
        ev = _OrderPlaced(order_id="1")
        wire = ev.to_dict()
        restored = _OrderPlaced.from_dict(wire)
        assert restored.id == ev.id
        assert isinstance(restored.id, UUID)

    def test_roundtrip_preserves_occurred_at(self) -> None:
        ev = _OrderPlaced(order_id="1", occurred_at=1_700_000_000.0)
        wire = ev.to_dict()
        restored = _OrderPlaced.from_dict(wire)
        assert abs(restored.occurred_at - ev.occurred_at) < 0.001

    def test_invalid_data_type_raises(self) -> None:
        with pytest.raises(ValueError, match="mapping"):
            _OrderPlaced.from_dict(
                {"id": str(uuid4()), "source": "//s",
                 "time": "2023-01-01T00:00:00+00:00", "data": "bad"},
            )
