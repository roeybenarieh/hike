"""
Abstract repository parity test suite.

Every concrete persistence provider should have a test class that inherits
from ``RepositoryParitySuite`` and supplies the following pytest fixtures:

- ``uow``         — a ``UnitOfWork`` wired to the provider's ``DBContext``
- ``repo``        — an ``IRepository[UUID, Boat, ...]`` for the provider
- ``journey_repo``— an ``IRepository[UUID, Journey, ...]`` for the provider

The tests here exercise only the public ``IRepository`` contract so that the
same assertions run against every provider.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from hike.entity import EntityID
from hike.persistence.ordering import asc, desc
from hike.persistence.pagination import CursorPagination, OffsetPagination, Page, PagePagination
from hike.persistence.repository import (
    ResourceAlreadyExistError,
    ResourceDoesNotExistError,
    IRepository,
    OptimisticLockError,
)
from hike.persistence.uow import UnitOfWork

from tests.hike.conftest import Boat, Checkpoint, Journey, Name, Price


class RepositoryParitySuite:
    """
    Parity test suite for :class:`~hike.persistence.repository.IRepository`.

    Subclasses must provide ``uow``, ``repo``, and ``journey_repo`` as pytest
    fixtures.  The base class does **not** declare them — pytest resolves them
    by name at collection time, so subclass fixtures may have any signature
    (accepting other fixtures as parameters) without causing type errors.
    """

    @pytest.fixture
    def fleet(self, uow: Any, repo: Any) -> list[Boat]:
        """Five boats with prices 10–50 and names A–E, committed to the repo."""
        boats = [
            Boat(name=Name(n), price=Price(p))
            for n, p in zip("ABCDE", [10.0, 20.0, 30.0, 40.0, 50.0])
        ]
        with uow(repo):
            for b in boats:
                repo.save(b)
            uow.commit()
        return boats

    # ------------------------------------------------------------------
    # Basic CRUD
    # ------------------------------------------------------------------

    def test_save_and_get_one(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        boat = Boat(name=Name("Sea Spirit"), price=Price(4_999.99))
        with uow(repo):
            repo.save(boat)
            uow.commit()
        with uow(repo):
            fetched = repo.get_one(boat.id)
        assert fetched.name == Name("Sea Spirit")
        assert fetched.price == Price(4_999.99)

    def test_update(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        boat = Boat(name=Name("Old Name"), price=Price(100.0))
        with uow(repo):
            repo.save(boat)
            uow.commit()
        boat.price = Price(200.0)
        with uow(repo):
            repo.update(boat)
            uow.commit()
        with uow(repo):
            fetched = repo.get_one(boat.id)
        assert fetched.price == Price(200.0)

    def test_get_many_with_spec(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        boat_a = Boat(name=Name("Alpha"), price=Price(10.0))
        boat_b = Boat(name=Name("Beta"), price=Price(50.0))
        with uow(repo):
            repo.save(boat_a)
            repo.save(boat_b)
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.price > 20.0)
        assert len(results) == 1
        assert results[0].name == Name("Beta")

    def test_count_with_spec(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("A"), price=Price(10.0)))
            repo.save(Boat(name=Name("B"), price=Price(50.0)))
            repo.save(Boat(name=Name("C"), price=Price(80.0)))
            uow.commit()
        with uow(repo):
            assert repo.count(Boat.price > 20.0) == 2
            assert repo.count(Boat.price > 100.0) == 0

    def test_upsert_creates_then_updates(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        boat = Boat(name=Name("Ghost"), price=Price(1.0))
        with uow(repo):
            repo.upsert(boat)
            uow.commit()
        boat.price = Price(2.0)
        with uow(repo):
            repo.upsert(boat)
            uow.commit()
        with uow(repo):
            fetched = repo.get_one(boat.id)
        assert fetched.price == Price(2.0)

    def test_delete(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        boat = Boat(name=Name("Doomed"), price=Price(0.01))
        with uow(repo):
            repo.save(boat)
            uow.commit()
        with uow(repo):
            repo.delete(boat)
            uow.commit()
        with uow(repo):
            with pytest.raises(ResourceDoesNotExistError):
                repo.get_one(boat.id)

    # ------------------------------------------------------------------
    # Error cases
    # ------------------------------------------------------------------

    def test_uuid_id_type_preserved_after_round_trip(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        boat = Boat(name=Name("Type Check"), price=Price(1.0))
        assert isinstance(boat.id.value, UUID), "precondition: Boat ID must be a UUID before save"
        with uow(repo):
            repo.save(boat)
            uow.commit()
        with uow(repo):
            fetched = repo.get_one(boat.id)
        assert isinstance(fetched.id.value, UUID), (
            f"Expected UUID after round-trip, got {type(fetched.id.value).__name__!r}"
        )

    def test_save_duplicate_raises(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        boat = Boat(name=Name("Twin"), price=Price(50.0))
        with uow(repo):
            repo.save(boat)
            uow.commit()
        with pytest.raises(ResourceAlreadyExistError):
            with uow(repo):
                repo.save(boat)
                uow.commit()

    def test_get_one_missing_raises(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            with pytest.raises(ResourceDoesNotExistError):
                repo.get_one(EntityID(uuid4()))

    def test_delete_missing_raises(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        ghost = Boat(name=Name("Never Saved"), price=Price(1.0))
        with uow(repo):
            with pytest.raises(ResourceDoesNotExistError):
                repo.delete(ghost)

    def test_rollback_on_exception(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        boat = Boat(name=Name("Rollback Boat"), price=Price(99.0))
        with pytest.raises(ValueError, match="simulated failure"):
            with uow(repo):
                repo.save(boat)
                raise ValueError("simulated failure")
        with uow(repo):
            with pytest.raises(ResourceDoesNotExistError):
                repo.get_one(boat.id)

    def test_optimistic_lock_conflict(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        boat = Boat(name=Name("Contested"), price=Price(100.0))
        with uow(repo):
            repo.save(boat)
            uow.commit()
        with uow(repo):
            copy_a = repo.get_one(boat.id)
        with uow(repo):
            copy_b = repo.get_one(boat.id)
        assert copy_a.get_version() == 0
        assert copy_b.get_version() == 0
        copy_a.price = Price(200.0)
        with uow(repo):
            repo.update(copy_a)
            uow.commit()
        assert copy_a.get_version() == 1
        copy_b.price = Price(300.0)
        with pytest.raises(OptimisticLockError):
            with uow(repo):
                repo.update(copy_b)
                uow.commit()

    # ------------------------------------------------------------------
    # Nested entities
    # ------------------------------------------------------------------

    def test_journey_save_and_get_one_with_checkpoints(
        self,
        uow: UnitOfWork[Any],
        journey_repo: IRepository[UUID, Journey, Any],
    ) -> None:
        cp1 = Checkpoint(name=Name("Paris"))
        cp2 = Checkpoint(name=Name("Lyon"))
        journey = Journey(name=Name("France Trip"), checkpoints=[cp1, cp2])
        with uow(journey_repo):
            journey_repo.save(journey)
            uow.commit()
        with uow(journey_repo):
            fetched = journey_repo.get_one(journey.id)
        assert fetched.name == Name("France Trip")
        assert len(fetched.checkpoints) == 2
        assert {cp.name for cp in fetched.checkpoints} == {Name("Paris"), Name("Lyon")}

    def test_journey_update_checkpoints(
        self,
        uow: UnitOfWork[Any],
        journey_repo: IRepository[UUID, Journey, Any],
    ) -> None:
        journey = Journey(name=Name("Tour"), checkpoints=[Checkpoint(name=Name("A"))])
        with uow(journey_repo):
            journey_repo.save(journey)
            uow.commit()
        journey.checkpoints.append(Checkpoint(name=Name("B")))
        with uow(journey_repo):
            journey_repo.update(journey)
            uow.commit()
        with uow(journey_repo):
            fetched = journey_repo.get_one(journey.id)
        assert len(fetched.checkpoints) == 2

    # ------------------------------------------------------------------
    # Ordering  (require `fleet` fixture via usefixtures to avoid unused-param warnings)
    # ------------------------------------------------------------------

    @pytest.mark.usefixtures("fleet")
    def test_ordering_price_asc(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            results = repo.get_many(Boat.price >= 0.0, ordering=[asc(Boat.price)])
        assert isinstance(results, list)
        prices = [b.price for b in results]
        assert prices == sorted(prices)

    @pytest.mark.usefixtures("fleet")
    def test_ordering_price_desc(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            results = repo.get_many(Boat.price >= 0.0, ordering=[desc(Boat.price)])
        assert isinstance(results, list)
        prices = [b.price for b in results]
        assert prices == sorted(prices, reverse=True)

    @pytest.mark.usefixtures("fleet")
    def test_ordering_name_asc(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            results = repo.get_many(Boat.price >= 0.0, ordering=[asc(Boat.name)])
        assert isinstance(results, list)
        names = [b.name for b in results]
        assert names == sorted(names)

    @pytest.mark.usefixtures("fleet")
    def test_ordering_single_orderby_shorthand(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            results = repo.get_many(Boat.price >= 0.0, ordering=asc(Boat.price))
        assert isinstance(results, list)
        prices = [b.price for b in results]
        assert prices == sorted(prices)

    @pytest.mark.usefixtures("fleet")
    def test_get_many_no_pagination_returns_list(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            results = repo.get_many(Boat.price >= 0.0)
        assert isinstance(results, list)
        assert len(results) == 5

    # ------------------------------------------------------------------
    # Offset pagination
    # ------------------------------------------------------------------

    @pytest.mark.usefixtures("fleet")
    def test_offset_pagination_first_page(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            page = repo.get_many(
                Boat.price >= 0.0,
                ordering=[asc(Boat.price)],
                pagination=OffsetPagination(offset=0, limit=2),
            )
        assert isinstance(page, Page)
        assert page.total == 5
        assert page.has_next is True
        assert [b.price for b in page.items] == [10.0, 20.0]

    @pytest.mark.usefixtures("fleet")
    def test_offset_pagination_middle_page(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            page = repo.get_many(
                Boat.price >= 0.0,
                ordering=[asc(Boat.price)],
                pagination=OffsetPagination(offset=2, limit=2),
            )
        assert isinstance(page, Page)
        assert page.total == 5
        assert page.has_next is True
        assert [b.price for b in page.items] == [30.0, 40.0]

    @pytest.mark.usefixtures("fleet")
    def test_offset_pagination_last_page(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            page = repo.get_many(
                Boat.price >= 0.0,
                ordering=[asc(Boat.price)],
                pagination=OffsetPagination(offset=4, limit=2),
            )
        assert isinstance(page, Page)
        assert page.total == 5
        assert page.has_next is False
        assert [b.price for b in page.items] == [50.0]

    @pytest.mark.usefixtures("fleet")
    def test_offset_pagination_beyond_end(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            page = repo.get_many(
                Boat.price >= 0.0,
                pagination=OffsetPagination(offset=10, limit=2),
            )
        assert isinstance(page, Page)
        assert page.items == []
        assert page.has_next is False

    @pytest.mark.usefixtures("fleet")
    def test_ordering_with_offset_pagination(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            page = repo.get_many(
                Boat.price >= 0.0,
                ordering=[desc(Boat.price)],
                pagination=OffsetPagination(offset=0, limit=2),
            )
        assert isinstance(page, Page)
        assert [b.price for b in page.items] == [50.0, 40.0]

    @pytest.mark.usefixtures("fleet")
    def test_spec_with_offset_pagination(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            page = repo.get_many(
                Boat.price > 20.0,
                ordering=[asc(Boat.price)],
                pagination=OffsetPagination(offset=0, limit=2),
            )
        assert isinstance(page, Page)
        assert page.total == 3
        assert [b.price for b in page.items] == [30.0, 40.0]

    # ------------------------------------------------------------------
    # Page pagination
    # ------------------------------------------------------------------

    @pytest.mark.usefixtures("fleet")
    def test_page_pagination_page1(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            page = repo.get_many(
                Boat.price >= 0.0,
                ordering=[asc(Boat.price)],
                pagination=PagePagination(page=1, page_size=2),
            )
        assert isinstance(page, Page)
        assert page.total == 5
        assert page.has_next is True
        assert [b.price for b in page.items] == [10.0, 20.0]

    @pytest.mark.usefixtures("fleet")
    def test_page_pagination_last_page(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            page = repo.get_many(
                Boat.price >= 0.0,
                ordering=[asc(Boat.price)],
                pagination=PagePagination(page=3, page_size=2),
            )
        assert isinstance(page, Page)
        assert page.total == 5
        assert page.has_next is False
        assert [b.price for b in page.items] == [50.0]

    # ------------------------------------------------------------------
    # Cursor pagination
    # ------------------------------------------------------------------

    @pytest.mark.usefixtures("fleet")
    def test_cursor_pagination_traverses_all(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        collected: list[Price] = []
        cursor: str | None = None
        for _ in range(10):
            with uow(repo):
                page = repo.get_many(
                    Boat.price >= 0.0,
                    ordering=[asc(Boat.price)],
                    pagination=CursorPagination(limit=2, cursor=cursor),
                )
            assert isinstance(page, Page)
            collected.extend(b.price for b in page.items)
            if not page.has_next:
                break
            cursor = page.next_cursor
        else:
            pytest.fail("Cursor pagination did not terminate")
        assert collected == [10.0, 20.0, 30.0, 40.0, 50.0]

    @pytest.mark.usefixtures("fleet")
    def test_cursor_pagination_first_page_has_next(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            page = repo.get_many(
                Boat.price >= 0.0,
                ordering=[asc(Boat.price)],
                pagination=CursorPagination(limit=2),
            )
        assert isinstance(page, Page)
        assert page.has_next is True
        assert page.next_cursor is not None
        assert page.total is None

    @pytest.mark.usefixtures("fleet")
    def test_cursor_pagination_last_page_no_next_cursor(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            first = repo.get_many(
                Boat.price >= 0.0,
                ordering=[asc(Boat.price)],
                pagination=CursorPagination(limit=4),
            )
        assert isinstance(first, Page)
        with uow(repo):
            last = repo.get_many(
                Boat.price >= 0.0,
                ordering=[asc(Boat.price)],
                pagination=CursorPagination(limit=4, cursor=first.next_cursor),
            )
        assert isinstance(last, Page)
        assert last.has_next is False
        assert last.next_cursor is None

    @pytest.mark.usefixtures("fleet")
    def test_ordering_with_cursor_pagination(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        collected: list[Price] = []
        cursor: str | None = None
        for _ in range(10):
            with uow(repo):
                page = repo.get_many(
                    Boat.price >= 0.0,
                    ordering=[desc(Boat.price)],
                    pagination=CursorPagination(limit=2, cursor=cursor),
                )
            assert isinstance(page, Page)
            collected.extend(b.price for b in page.items)
            if not page.has_next:
                break
            cursor = page.next_cursor
        assert collected == [50.0, 40.0, 30.0, 20.0, 10.0]
