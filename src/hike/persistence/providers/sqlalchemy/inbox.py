from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import cast

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from hike.entity import EntityID
from hike.persistence.inbox import IInboxRepository, InboxRecord
from hike.persistence.ordering import OrderBy
from hike.persistence.pagination import Page, Pagination
from hike.persistence.repository import AggregateDoesNotExistError
from hike.specifications import ISpecification


class _InboxBase(DeclarativeBase): ...


class InboxModel(_InboxBase):
    """SQLAlchemy ORM model for the inbox table."""

    __tablename__ = "hike_inbox"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    event_data: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


def _row_to_record(row: InboxModel) -> InboxRecord:
    return InboxRecord(
        id=EntityID(row.event_id),
        event_type=row.event_type,
        event_data=row.event_data,
        received_at=row.received_at,
        processed=row.processed,
    )


class SQLAlchemyInboxRepository(IInboxRepository):
    """Inbox repository backed by SQLAlchemy.

    Uses its own session — the inbox table is managed independently from
    domain aggregate changes (it belongs to the *receiving* bounded context).

    Call ``create_tables(engine)`` once during application startup.
    """

    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session  # type: ignore[assignment]

    @staticmethod
    def create_tables(engine: object) -> None:
        _InboxBase.metadata.create_all(engine)  # type: ignore[arg-type]

    def save(self, aggregate: InboxRecord) -> str:
        session: Session = cast(Session, self._session)
        session.add(InboxModel(
            event_id=aggregate.id.value,
            event_type=aggregate.event_type,
            event_data=aggregate.event_data,
            received_at=aggregate.received_at,
            processed=aggregate.processed,
        ))
        session.flush()
        self._collect_events(aggregate)
        return aggregate.id  # pyright: ignore[reportReturnType]

    def get_one(self, identifier: EntityID[str]) -> InboxRecord:
        session: Session = cast(Session, self._session)
        row = session.get(InboxModel, identifier.value)
        if row is None:
            raise AggregateDoesNotExistError(identifier)
        return _row_to_record(row)

    def upsert(self, aggregate: InboxRecord) -> None:
        session: Session = cast(Session, self._session)
        row = session.get(InboxModel, aggregate.id.value)
        if row is None:
            session.add(InboxModel(
                event_id=aggregate.id.value,
                event_type=aggregate.event_type,
                event_data=aggregate.event_data,
                received_at=aggregate.received_at,
                processed=aggregate.processed,
            ))
        else:
            row.processed = aggregate.processed
            row.event_type = aggregate.event_type
            row.event_data = aggregate.event_data
            row.received_at = aggregate.received_at
        session.commit()
        self._collect_events(aggregate)

    def _delete(self, identifier: EntityID[str]) -> None:
        session: Session = cast(Session, self._session)
        row = session.get(InboxModel, identifier.value)
        if row is None:
            raise AggregateDoesNotExistError(identifier)
        session.delete(row)

    def _get_many(
        self,
        specification: ISpecification,
        *,
        ordering: Sequence[OrderBy] | None = None,
        pagination: Pagination | None = None,
    ) -> list[InboxRecord] | Page[InboxRecord]:
        raise NotImplementedError

    def update(self, aggregate: InboxRecord) -> None:
        raise NotImplementedError

    def count(self, specification: ISpecification) -> int:
        raise NotImplementedError
