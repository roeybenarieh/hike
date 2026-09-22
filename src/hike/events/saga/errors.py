from __future__ import annotations

from hike.domain_event import Event


class SagaNotFoundError(Exception):
    """Raised by ``SagaManager.handle`` when a ``@handles`` handler fires but no
    correlated saga instance exists in the repository.

    This typically means the starting event (handled by a ``@started_by`` method)
    was never received, or the saga has already completed and been deleted.
    Catch it at the subscriber level to dead-letter or log the orphaned event::

        try:
            manager.handle(event)
        except SagaNotFoundError as exc:
            logger.warning("orphaned event %s — no saga found", exc.event_type.__name__)
    """

    def __init__(self, event_type: type[Event], event: Event) -> None:
        self.event_type = event_type
        self.event = event
        super().__init__(
            f"No saga instance found for {event_type.__name__} "
            f"(id={getattr(event, 'id', '?')}). "
            f"Decorate a handler with @started_by to allow creating a new saga, "
            f"or ensure the saga was already started before this event was received."
        )
