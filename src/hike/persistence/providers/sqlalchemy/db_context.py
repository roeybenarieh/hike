from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from hike.persistence.uow import DBContext


class SQLAlchemyDBContext(DBContext[Session]):
    """DBContext backed by a SQLAlchemy Session.

    Usage::

        factory = sessionmaker(bind=engine)
        ctx = SQLAlchemyDBContext(factory)
        with UnitOfWork(repos, ctx):
            repo.add(aggregate)
            uow.commit()
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__()
        self._session_factory = session_factory

    def begin(self) -> None:
        self._session = self._session_factory()

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def close(self) -> None:
        self.session.close()
        self._session = None
