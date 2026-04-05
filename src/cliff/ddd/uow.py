from abc import ABC, abstractmethod
from typing import NamedTuple, Self

from .repository import IRepository


class DBContext(ABC):
    @abstractmethod
    def begin(self):
        """Begin a transaction."""

    @abstractmethod
    def commit(self) -> None:
        """Commit a transaction."""

    @abstractmethod
    def rollback(self) -> None:
        """Rollback a transaction. abort any uncommited changes"""

    @abstractmethod
    def close(self) -> None:
        """Close a transaction. Use after finishing a transaction successfully"""


class UnitOfWork[TRepos: NamedTuple]:
    def __init__(self, repos: TRepos):
        for repo in repos:
            if not issubclass(repo, IRepository):
                raise ValueError(
                    f"the provided {repo=} it not a subclass of {type(IRepository)}"
                )
        self.repos = repos

    def __enter__(self, db_context: DBContext, auto_commit=False) -> Self:
        self.auto_commit = auto_commit
        self._context = db_context
        self._context.begin()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type:
            self._context.rollback()
            return
        if self.auto_commit:
            self._context.commit()
            return
        self._context.close()

    def commit(self):
        self._context.commit()


from .aggregate import Aggregate, AggregateUUID


class First(Aggregate[AggregateUUID]): ...


class Second(Aggregate[AggregateUUID]): ...


def s() -> IRepository: ...


def m() -> DBContext: ...


from .aggregate import AggregateID


class AggregateStrID(AggregateID[str]): ...


class MyRepos(NamedTuple):
    first: IRepository[AggregateStrID, First]
    second: IRepository[AggregateUUID, Second]


my_uow = UnitOfWork[MyRepos](MyRepos(first=s(), second=s()))
my_uow.repos.first.save()
