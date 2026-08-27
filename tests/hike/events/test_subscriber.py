"""Unit tests for IExternalEventSubscriber internals (subscriber.py)."""
from dataclasses import dataclass
from uuid import uuid4

import pytest

from hike.events.integration_event import IntegrationEvent
from hike.events.interfaces import IEventHandler
from hike.events.interfaces.background_task import Task
from hike.events.interfaces.subscriber import IExternalEventSubscriber, _event_type_for  # pyright: ignore[reportPrivateUsage]


@dataclass(frozen=True, kw_only=True)
class _PingEvent(IntegrationEvent):
    payload: str
    version: int = 1


@dataclass(frozen=True, kw_only=True)
class _PongEvent(IntegrationEvent):
    payload: str
    version: int = 1

_dispatch = "_dispatch_to_handlers"
_hdict = "_handlers"


class _Subscriber(IExternalEventSubscriber[IntegrationEvent]):
    def _subscribe(self, event_type: str, event_class: type[IntegrationEvent], event_handler: IEventHandler[IntegrationEvent]) -> None:
        super()._subscribe(event_type, event_class, event_handler)

    def cleanup(self) -> None:
        pass

    def tasks(self) -> list[Task]:
        return []


def _make_sub() -> IExternalEventSubscriber[IntegrationEvent]:
    """Return a subscriber typed as the base to give Pyright full method visibility."""
    return _Subscriber()  # pyright: ignore[reportAbstractUsage]


class TestEventTypeFor:
    def test_resolves_from_generic_base(self) -> None:
        class _H(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None: ...

        assert _event_type_for(_H()) is _PingEvent

    def test_falls_back_to_handle_annotation(self) -> None:
        class _H(IEventHandler):  # type: ignore[type-arg]
            def handle(self, event: _PingEvent) -> None: ...  # type: ignore[override]

        assert _event_type_for(_H()) is _PingEvent

    def test_raises_when_handle_only_annotated_with_base_event(self) -> None:
        from hike.domain_event import Event
        class _H(IEventHandler):  # type: ignore[type-arg]
            def handle(self, event: Event) -> None: ...  # type: ignore[override]

        with pytest.raises(TypeError):
            _event_type_for(_H())

    def test_raises_when_no_type_info_at_all(self) -> None:
        class _H(IEventHandler):  # type: ignore[type-arg]
            def handle(self, event: object) -> None: ...  # type: ignore[override]

        with pytest.raises(TypeError):
            _event_type_for(_H())


class TestDispatchToHandlers:
    def test_calls_handler_for_matching_event_type(self) -> None:
        sub = _make_sub()
        received: list[_PingEvent] = []

        class _H(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:
                received.append(event)

        sub.subscribe(_H())  # type: ignore[arg-type]
        event = _PingEvent(payload="hi")
        getattr(sub, _dispatch)("_PingEvent", event)
        assert received == [event]

    def test_deduplicates_same_event_id(self) -> None:
        sub = _make_sub()
        count = 0

        class _H(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:
                nonlocal count
                count += 1

        sub.subscribe(_H())  # type: ignore[arg-type]
        shared_id = uuid4()
        getattr(sub, _dispatch)("_PingEvent", _PingEvent(payload="a", id=shared_id))
        getattr(sub, _dispatch)("_PingEvent", _PingEvent(payload="b", id=shared_id))
        assert count == 1

    def test_different_ids_are_not_deduplicated(self) -> None:
        sub = _make_sub()
        count = 0

        class _H(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:
                nonlocal count
                count += 1

        sub.subscribe(_H())  # type: ignore[arg-type]
        getattr(sub, _dispatch)("_PingEvent", _PingEvent(payload="a"))
        getattr(sub, _dispatch)("_PingEvent", _PingEvent(payload="b"))
        assert count == 2

    def test_skips_event_type_with_no_handlers(self) -> None:
        sub = _make_sub()
        received: list[_PingEvent] = []

        class _H(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:
                received.append(event)

        sub.subscribe(_H())  # type: ignore[arg-type]
        getattr(sub, _dispatch)("_PongEvent", _PongEvent(payload="x"))
        assert not received

    def test_reraises_handler_exception(self) -> None:
        sub = _make_sub()

        class _Fail(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None:
                raise RuntimeError("boom")

        sub.subscribe(_Fail())  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="boom"):
            getattr(sub, _dispatch)("_PingEvent", _PingEvent(payload="x"))


class TestSubscribe:
    def test_auto_detects_event_type_from_generic(self) -> None:
        sub = _make_sub()

        class _H(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None: ...

        h = _H()
        sub.subscribe(h)  # type: ignore[arg-type]
        assert h in getattr(sub, _hdict)["_PingEvent"]

    def test_explicit_event_type_string_overrides_detection(self) -> None:
        sub = _make_sub()

        class _H(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None: ...

        h = _H()
        sub.subscribe("CustomType", h)  # type: ignore[arg-type]
        assert h in getattr(sub, _hdict)["CustomType"]

    def test_multiple_subscriptions_accumulate(self) -> None:
        sub = _make_sub()

        class _H1(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None: ...

        class _H2(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None: ...

        h1, h2 = _H1(), _H2()
        sub.subscribe(h1)  # type: ignore[arg-type]
        sub.subscribe(h2)  # type: ignore[arg-type]
        assert getattr(sub, _hdict)["_PingEvent"] == [h1, h2]

    def test_on_subscribe_hook_called(self) -> None:
        calls: list[tuple[str, type]] = []

        class _Tracking(_Subscriber):
            def _on_subscribe(self, event_type_name: str, event_type: type[IntegrationEvent]) -> None:
                calls.append((event_type_name, event_type))

        class _H(IEventHandler[_PingEvent]):
            def handle(self, event: _PingEvent) -> None: ...

        sub = _Tracking()
        sub.subscribe(_H())  # type: ignore[arg-type]
        assert calls == [("_PingEvent", _PingEvent)]
