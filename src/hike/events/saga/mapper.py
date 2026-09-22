from __future__ import annotations

from typing import TYPE_CHECKING

from hike.domain_event import Event

if TYPE_CHECKING:
    from .data import SagaData


class SagaFieldMapper[TSagaData: SagaData]:
    """Fluent builder returned by ``SagaMapper.map_saga``.

    Each call to ``to_message`` registers one rule: when an event of
    ``msg_type`` arrives, ``SagaManager`` reads ``msg_field`` from the event
    and queries the repository for a saga whose ``saga_field`` equals that
    value.  Calls chain so multiple event types can share the same saga field::

        mapper.map_saga("order_id") \\
            .to_message(OrderPlaced,    "order_id") \\
            .to_message(PaymentDone,    "order_id") \\
            .to_message(ShipmentSent,   "order_id")
    """

    def __init__(self, mapper: SagaMapper[TSagaData], saga_field: str) -> None:
        self._mapper = mapper
        self._saga_field = saga_field

    def to_message[TEvent: Event](
        self,
        msg_type: type[TEvent],
        msg_field: str,
    ) -> SagaFieldMapper[TSagaData]:
        """Register a correlation rule: ``msg_type.msg_field`` identifies the saga instance.

        ``SagaManager`` uses the rule to look up (or create) the saga data row
        before dispatching the event to the handler.  Returns ``self`` so calls
        can be chained.
        """
        self._mapper.rules[msg_type] = (self._saga_field, msg_field)
        return self


class SagaMapper[TSagaData: SagaData]:
    """Accumulates correlation rules that tell ``SagaManager`` how to find a saga instance.

    Passed to ``Saga.configure_how_to_find_saga`` at construction time.  Use
    the fluent ``map_saga(...).to_message(...)`` DSL to declare which field on
    each incoming event identifies the saga::

        def configure_how_to_find_saga(self, mapper: SagaMapper) -> None:
            mapper.map_saga("order_id") \\
                .to_message(OrderPlaced,  "order_id") \\
                .to_message(PaymentDone,  "order_id")

            mapper.map_saga("shipment_id") \\
                .to_message(ShipmentSent, "id")
    """

    def __init__(self) -> None:
        self.rules: dict[type[Event], tuple[str, str]] = {}

    def map_saga(self, saga_field: str) -> SagaFieldMapper[TSagaData]:
        """Begin mapping *saga_field* to one or more incoming event fields.

        Returns a :class:`SagaFieldMapper` whose ``to_message`` calls register
        the actual rules.
        """
        return SagaFieldMapper(self, saga_field)
