"""Integration tests for UnitOfWork + InMemoryRepository."""
from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from hike.ddd.pagination import CursorPagination, OffsetPagination, Page, PagePagination
from hike.ddd.providers.in_memory import InMemoryDBContext, InMemoryRepository
from hike.ddd.repository import (
    AggregateAlreadyExistError,
    AggregateDoesNotExistError,
    OptimisticLockError,
    get_version,
)
from hike.ddd.uow import UnitOfWork

from tests.hike.ddd.conftest import Boat, Checkpoint, Journey, Name, Price


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_journey_uow() -> UnitOfWork[dict[Any, Any], UUID, Journey]:
    context = InMemoryDBContext()
    repo: InMemoryRepository[UUID, Journey] = InMemoryRepository()
    return UnitOfWork(context, repo=repo)


def make_uow() -> UnitOfWork[dict[Any, Any], UUID, Boat]:
    context = InMemoryDBContext()
    repo: InMemoryRepository[UUID, Boat] = InMemoryRepository()
    return UnitOfWork(context, repo=repo)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_save_and_get_one() -> None:
    boat = Boat(name=Name("Sea Spirit"), price=Price(4_999.99))
    uow = make_uow()

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        fetched = uow.repo.get_one(boat.id)

    assert fetched.name == Name("Sea Spirit")
    assert fetched.price == Price(4_999.99)


def test_update() -> None:
    boat = Boat(name=Name("Old Name"), price=Price(100.0))
    uow = make_uow()

    with uow:
        uow.repo.save(boat)
        uow.commit()

    boat.price = Price(200.0)
    with uow:
        uow.repo.update(boat)
        uow.commit()

    with uow:
        fetched = uow.repo.get_one(boat.id)

    assert fetched.price == Price(200.0)


def test_get_many_with_spec() -> None:
    boat_a = Boat(name=Name("Alpha"), price=Price(10.0))
    boat_b = Boat(name=Name("Beta"), price=Price(50.0))
    uow = make_uow()

    with uow:
        uow.repo.save(boat_a)
        uow.repo.save(boat_b)
        uow.commit()

    with uow:
        results = uow.repo.get_many(Boat.price > 20.0)

    assert len(results) == 1
    assert results[0].name == Name("Beta")


def test_count_with_spec() -> None:
    boat_a = Boat(name=Name("Alpha"), price=Price(10.0))
    boat_b = Boat(name=Name("Beta"), price=Price(50.0))
    boat_c = Boat(name=Name("Gamma"), price=Price(80.0))
    uow = make_uow()

    with uow:
        uow.repo.save(boat_a)
        uow.repo.save(boat_b)
        uow.repo.save(boat_c)
        uow.commit()

    with uow:
        assert uow.repo.count(Boat.price > 20.0) == 2

    with uow:
        assert uow.repo.count(Boat.price > 100.0) == 0


def test_upsert_creates_then_updates() -> None:
    boat = Boat(name=Name("Ghost"), price=Price(1.0))
    uow = make_uow()

    with uow:
        uow.repo.upsert(boat)  # create
        uow.commit()

    boat.price = Price(2.0)
    with uow:
        uow.repo.upsert(boat)  # update
        uow.commit()

    with uow:
        fetched = uow.repo.get_one(boat.id)

    assert fetched.price == Price(2.0)


def test_delete() -> None:
    boat = Boat(name=Name("Doomed"), price=Price(0.01))
    uow = make_uow()

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        uow.repo.delete(boat)
        uow.commit()

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(boat.id)


def test_save_duplicate_raises() -> None:
    boat = Boat(name=Name("Twin"), price=Price(50.0))
    uow = make_uow()

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with pytest.raises(AggregateAlreadyExistError):
        with uow:
            uow.repo.save(boat)
            uow.commit()


def test_rollback_on_exception() -> None:
    """Exception inside the UoW block must abort the transaction."""
    boat = Boat(name=Name("Rollback Boat"), price=Price(99.0))
    uow = make_uow()

    with pytest.raises(ValueError, match="simulated failure"):
        with uow:
            uow.repo.save(boat)
            raise ValueError("simulated failure")

    with uow:
        with pytest.raises(AggregateDoesNotExistError):
            uow.repo.get_one(boat.id)


def test_optimistic_lock_conflict() -> None:
    """Second writer loses when it holds a stale version."""
    boat = Boat(name=Name("Contested"), price=Price(100.0))
    uow = make_uow()

    with uow:
        uow.repo.save(boat)
        uow.commit()

    with uow:
        copy_a = uow.repo.get_one(boat.id)
    with uow:
        copy_b = uow.repo.get_one(boat.id)

    assert get_version(copy_a) == 0
    assert get_version(copy_b) == 0

    copy_a.price = Price(200.0)
    with uow:
        uow.repo.update(copy_a)
        uow.commit()
    assert get_version(copy_a) == 1

    copy_b.price = Price(300.0)
    with pytest.raises(OptimisticLockError):
        with uow:
            uow.repo.update(copy_b)
            uow.commit()


def test_journey_save_and_get_one_with_checkpoints() -> None:
    uow = make_journey_uow()
    cp1 = Checkpoint(name=Name("Paris"))
    cp2 = Checkpoint(name=Name("Lyon"))
    journey = Journey(name=Name("France Trip"), checkpoints=[cp1, cp2])

    with uow:
        uow.repo.save(journey)
        uow.commit()

    with uow:
        fetched = uow.repo.get_one(journey.id)

    assert fetched.name == Name("France Trip")
    assert len(fetched.checkpoints) == 2
    assert {cp.name for cp in fetched.checkpoints} == {Name("Paris"), Name("Lyon")}


def test_journey_update_checkpoints() -> None:
    uow = make_journey_uow()
    journey = Journey(name=Name("Tour"), checkpoints=[Checkpoint(name=Name("A"))])

    with uow:
        uow.repo.save(journey)
        uow.commit()

    journey.checkpoints.append(Checkpoint(name=Name("B")))
    with uow:
        uow.repo.update(journey)
        uow.commit()

    with uow:
        fetched = uow.repo.get_one(journey.id)

    assert len(fetched.checkpoints) == 2


# ---------------------------------------------------------------------------
# Ordering tests
# ---------------------------------------------------------------------------

def _make_fleet() -> tuple[UnitOfWork[dict[Any, Any], UUID, Boat], list[Boat]]:
    """Return (uow, boats) with prices [10, 20, 30, 40, 50] and names A–E."""
    boats = [
        Boat(name=Name(n), price=Price(p))
        for n, p in zip("ABCDE", [10.0, 20.0, 30.0, 40.0, 50.0])
    ]
    uow = make_uow()
    with uow:
        for b in boats:
            uow.repo.save(b)
        uow.commit()
    return uow, boats


def test_ordering_price_asc() -> None:
    uow, _ = _make_fleet()
    with uow:
        results = uow.repo.get_many(Boat.price >= 0.0, ordering=[Boat.price.asc()])
    assert isinstance(results, list)
    prices = [b.price.value for b in results]
    assert prices == sorted(prices)


def test_ordering_price_desc() -> None:
    uow, _ = _make_fleet()
    with uow:
        results = uow.repo.get_many(Boat.price >= 0.0, ordering=[Boat.price.desc()])
    assert isinstance(results, list)
    prices = [b.price.value for b in results]
    assert prices == sorted(prices, reverse=True)


def test_ordering_name_asc() -> None:
    uow, _ = _make_fleet()
    with uow:
        results = uow.repo.get_many(Boat.price >= 0.0, ordering=[Boat.name.asc()])
    assert isinstance(results, list)
    names = [b.name.value for b in results]
    assert names == sorted(names)


def test_ordering_single_orderby_shorthand() -> None:
    uow, _ = _make_fleet()
    with uow:
        results = uow.repo.get_many(Boat.price >= 0.0, ordering=Boat.price.asc())
    assert isinstance(results, list)
    prices = [b.price.value for b in results]
    assert prices == sorted(prices)


def test_get_many_no_pagination_returns_list() -> None:
    uow, _ = _make_fleet()
    with uow:
        results = uow.repo.get_many(Boat.price >= 0.0)
    assert isinstance(results, list)
    assert len(results) == 5


def test_get_many_ordering_only_returns_list() -> None:
    uow, _ = _make_fleet()
    with uow:
        results = uow.repo.get_many(Boat.price >= 0.0, ordering=[Boat.price.asc()])
    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Offset pagination tests
# ---------------------------------------------------------------------------

def test_offset_pagination_first_page() -> None:
    uow, _ = _make_fleet()
    with uow:
        page = uow.repo.get_many(
            Boat.price >= 0.0,
            ordering=[Boat.price.asc()],
            pagination=OffsetPagination(offset=0, limit=2),
        )
    assert isinstance(page, Page)
    assert page.total == 5
    assert page.has_next is True
    assert [b.price.value for b in page.items] == [10.0, 20.0]


def test_offset_pagination_middle_page() -> None:
    uow, _ = _make_fleet()
    with uow:
        page = uow.repo.get_many(
            Boat.price >= 0.0,
            ordering=[Boat.price.asc()],
            pagination=OffsetPagination(offset=2, limit=2),
        )
    assert isinstance(page, Page)
    assert page.total == 5
    assert page.has_next is True
    assert [b.price.value for b in page.items] == [30.0, 40.0]


def test_offset_pagination_last_page() -> None:
    uow, _ = _make_fleet()
    with uow:
        page = uow.repo.get_many(
            Boat.price >= 0.0,
            ordering=[Boat.price.asc()],
            pagination=OffsetPagination(offset=4, limit=2),
        )
    assert isinstance(page, Page)
    assert page.total == 5
    assert page.has_next is False
    assert [b.price.value for b in page.items] == [50.0]


def test_offset_pagination_exact_fit() -> None:
    uow, _ = _make_fleet()
    with uow:
        page = uow.repo.get_many(
            Boat.price >= 0.0,
            ordering=[Boat.price.asc()],
            pagination=OffsetPagination(offset=0, limit=5),
        )
    assert isinstance(page, Page)
    assert page.has_next is False
    assert len(page.items) == 5


def test_offset_pagination_beyond_end() -> None:
    uow, _ = _make_fleet()
    with uow:
        page = uow.repo.get_many(
            Boat.price >= 0.0,
            pagination=OffsetPagination(offset=10, limit=2),
        )
    assert isinstance(page, Page)
    assert page.items == []
    assert page.has_next is False


# ---------------------------------------------------------------------------
# Page pagination tests
# ---------------------------------------------------------------------------

def test_page_pagination_page1() -> None:
    uow, _ = _make_fleet()
    with uow:
        page = uow.repo.get_many(
            Boat.price >= 0.0,
            ordering=[Boat.price.asc()],
            pagination=PagePagination(page=1, page_size=2),
        )
    assert isinstance(page, Page)
    assert page.total == 5
    assert page.has_next is True
    assert [b.price.value for b in page.items] == [10.0, 20.0]


def test_page_pagination_last_page() -> None:
    uow, _ = _make_fleet()
    with uow:
        page = uow.repo.get_many(
            Boat.price >= 0.0,
            ordering=[Boat.price.asc()],
            pagination=PagePagination(page=3, page_size=2),
        )
    assert isinstance(page, Page)
    assert page.total == 5
    assert page.has_next is False
    assert [b.price.value for b in page.items] == [50.0]


# ---------------------------------------------------------------------------
# Cursor pagination tests
# ---------------------------------------------------------------------------

def test_cursor_pagination_traverses_all_pages() -> None:
    uow, _ = _make_fleet()
    collected: list[float] = []
    cursor: str | None = None

    for _ in range(10):  # guard against infinite loops
        with uow:
            page = uow.repo.get_many(
                Boat.price >= 0.0,
                ordering=[Boat.price.asc()],
                pagination=CursorPagination(limit=2, cursor=cursor),
            )
        assert isinstance(page, Page)
        collected.extend(b.price.value for b in page.items)
        if not page.has_next:
            break
        cursor = page.next_cursor
    else:
        pytest.fail("Cursor pagination did not terminate")

    assert collected == [10.0, 20.0, 30.0, 40.0, 50.0]


def test_cursor_pagination_first_page_has_next() -> None:
    uow, _ = _make_fleet()
    with uow:
        page = uow.repo.get_many(
            Boat.price >= 0.0,
            ordering=[Boat.price.asc()],
            pagination=CursorPagination(limit=2),
        )
    assert isinstance(page, Page)
    assert page.has_next is True
    assert page.next_cursor is not None
    assert page.total is None


def test_cursor_pagination_last_page_no_next_cursor() -> None:
    uow, _ = _make_fleet()
    # Skip to last page
    with uow:
        first = uow.repo.get_many(
            Boat.price >= 0.0,
            ordering=[Boat.price.asc()],
            pagination=CursorPagination(limit=4),
        )
    assert isinstance(first, Page)
    with uow:
        last = uow.repo.get_many(
            Boat.price >= 0.0,
            ordering=[Boat.price.asc()],
            pagination=CursorPagination(limit=4, cursor=first.next_cursor),
        )
    assert isinstance(last, Page)
    assert last.has_next is False
    assert last.next_cursor is None


# ---------------------------------------------------------------------------
# Combined ordering + pagination tests
# ---------------------------------------------------------------------------

def test_ordering_with_offset_pagination() -> None:
    uow, _ = _make_fleet()
    with uow:
        page = uow.repo.get_many(
            Boat.price >= 0.0,
            ordering=[Boat.price.desc()],
            pagination=OffsetPagination(offset=0, limit=2),
        )
    assert isinstance(page, Page)
    assert [b.price.value for b in page.items] == [50.0, 40.0]


def test_spec_with_offset_pagination() -> None:
    uow, _ = _make_fleet()
    with uow:
        page = uow.repo.get_many(
            Boat.price > 20.0,
            ordering=[Boat.price.asc()],
            pagination=OffsetPagination(offset=0, limit=2),
        )
    assert isinstance(page, Page)
    assert page.total == 3  # 30, 40, 50
    assert [b.price.value for b in page.items] == [30.0, 40.0]


def test_ordering_with_cursor_pagination() -> None:
    uow, _ = _make_fleet()
    collected: list[float] = []
    cursor: str | None = None

    for _ in range(10):
        with uow:
            page = uow.repo.get_many(
                Boat.price >= 0.0,
                ordering=[Boat.price.desc()],
                pagination=CursorPagination(limit=2, cursor=cursor),
            )
        assert isinstance(page, Page)
        collected.extend(b.price.value for b in page.items)
        if not page.has_next:
            break
        cursor = page.next_cursor

    assert collected == [50.0, 40.0, 30.0, 20.0, 10.0]
