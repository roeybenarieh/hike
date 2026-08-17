from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String, Text, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from hike.entity import EntityID
from hike.persistence.ordering import OrderBy
from hike.persistence.outbox import IOutboxRepository, OutboxRecord
from hike.persistence.pagination import Page, Pagination
from hike.persistence.repository import AggregateDoesNotExistError
from hike.specifications import ISpecification


class _OutboxBase(DeclarativeBase): ...


class OutboxModel(_OutboxBase):
    """SQLAlchemy ORM model for the outbox table."""

    __tablename__ = "hike_outbox"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    event_data: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _row_to_record(row: OutboxModel) -> OutboxRecord:
    return OutboxRecord(
        id=EntityID(row.id),
        event_type=row.event_type,
        event_data=row.event_data,
        created_at=row.created_at,
    )


class SQLAlchemyOutboxRepository(IOutboxRepository):
    """Outbox repository backed by SQLAlchemy.

    The session is injected by ``UnitOfWork.__enter__`` so that ``save_all``
    writes in the same transaction as domain aggregate changes.

    Call ``create_tables(engine)`` once during application startup to create
    the ``hike_outbox`` table, or include ``OutboxModel.metadata`` in your
    existing ``Base.metadata.create_all`` call.
    """

    @staticmethod
    def create_tables(engine: Any) -> None:
        _OutboxBase.metadata.create_all(engine)

    def save(self, aggregate: OutboxRecord) -> UUID:
        session: Session = cast(Session, self._session)
        session.add(OutboxModel(
            id=aggregate.id.value,
            event_type=aggregate.event_type,
            event_data=aggregate.event_data,
            created_at=aggregate.created_at,
        ))
        self._collect_events(aggregate)
        return aggregate.id  # pyright: ignore[reportReturnType]

    def _delete(self, identifier: EntityID[UUID]) -> None:
        session: Session = cast(Session, self._session)
        row = session.get(OutboxModel, identifier.value)
        if row is None:
            raise AggregateDoesNotExistError(identifier)
        session.delete(row)
        session.commit()

    def get_one(self, identifier: EntityID[UUID]) -> OutboxRecord:
        session: Session = cast(Session, self._session)
        row = session.get(OutboxModel, identifier.value)
        if row is None:
            raise AggregateDoesNotExistError(identifier)
        return _row_to_record(row)

    def get_pending(self) -> list[OutboxRecord]:
        from sqlalchemy import select
        session: Session = cast(Session, self._session)
        rows = session.scalars(select(OutboxModel).order_by(OutboxModel.created_at)).all()
        return [_row_to_record(row) for row in rows]

    def _get_many(
        self,
        specification: ISpecification,
        *,
        ordering: Sequence[OrderBy] | None = None,
        pagination: Pagination | None = None,
    ) -> list[OutboxRecord] | Page[OutboxRecord]:
        raise NotImplementedError

    def update(self, aggregate: OutboxRecord) -> None:
        raise NotImplementedError

    def count(self, specification: ISpecification) -> int:
        raise NotImplementedError

    def upsert(self, aggregate: OutboxRecord) -> None:
        raise NotImplementedError
