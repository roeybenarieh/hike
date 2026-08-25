import queue
from collections.abc import Iterator, Sequence
from copy import deepcopy
from typing import Any, cast

from hike.persistence.ordering import OrderBy, apply_ordering_in_memory, get_field_value
from hike.persistence.pagination import (
    OffsetPagination,
    Page,
    PagePagination,
    Pagination,
    cursor_position,
    decode_cursor,
    encode_cursor,
)
from hike.persistence.persistable import Persistable
from hike.persistence.repository import (
    ResourceAlreadyExistError,
    ResourceDoesNotExistError,
    IAggregateRepository,
    IRepository,
    OptimisticLockError,
    TAggregate,
    TId,
    TPersistable,
)
from hike.specifications import ISpecification

_WATCH_POLL = 1.0  # seconds — how long each Queue.get() blocks before re-checking the deadline


class InMemoryPersistableRepository(IRepository[TId, TPersistable, dict[Any, Persistable[Any]]]):
    """In-memory repository for any ``Persistable`` object.

    Stores deepcopies of objects in the session dict (keyed by raw
    ``obj.id.value``) so that in-memory instances are isolated from each other,
    enabling version-based optimistic concurrency checks.

    Subclass and mix in ``IAggregateRepository`` to add domain-event collection
    (see ``InMemoryRepository``).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._insert_queues: list[queue.Queue[TPersistable]] = []

    def _prepare_stored_copy(self, copy: TPersistable) -> None:
        """Called on the deep-copied object before it is stored in the session.

        Default: no-op.  Override to mutate the copy before storage
        (e.g. ``InMemoryRepository`` calls ``copy.clear_events()`` here).
        """

    def save(self, obj: TPersistable) -> TId:
        key = obj.get_id()
        if key in self.session:
            raise ResourceAlreadyExistError(obj)
        copy = deepcopy(obj)
        copy.set_version(0)
        self._prepare_stored_copy(copy)
        self.session[key] = copy
        obj.set_version(0)
        self._after_mutate(obj)
        if self._insert_queues:
            snapshot = deepcopy(obj)
            for q in self._insert_queues:
                q.put(snapshot)
        return obj.get_id()  # pyright: ignore[reportReturnType]

    def _delete(self, identifier: TId) -> None:
        key = identifier
        if key not in self.session:
            raise ResourceDoesNotExistError(identifier)
        del self.session[key]

    def get_one(self, identifier: TId) -> TPersistable:
        obj = self.session.get(identifier)
        if obj is None:
            raise ResourceDoesNotExistError(identifier)
        return deepcopy(cast(TPersistable, obj))

    def _get_many(
        self,
        specification: ISpecification,
        *,
        ordering: Sequence[OrderBy] | None = None,
        pagination: Pagination | None = None,
    ) -> list[TPersistable] | Page[TPersistable]:
        matched: list[TPersistable] = [
            deepcopy(cast(TPersistable, obj))
            for obj in self.session.values()
            if specification.is_satisfied(obj)
        ]

        ordering_list = list(ordering) if ordering else []
        if ordering_list:
            matched = apply_ordering_in_memory(matched, ordering_list)

        if pagination is None:
            return matched

        if isinstance(pagination, OffsetPagination):
            offset = pagination.offset
            limit = pagination.limit
            total = len(matched)
            page_items = matched[offset : offset + limit]
            return Page(items=page_items, total=total, has_next=(offset + limit) < total)

        if isinstance(pagination, PagePagination):
            offset = pagination.offset
            limit = pagination.page_size
            total = len(matched)
            page_items = matched[offset : offset + limit]
            return Page(items=page_items, total=total, has_next=(offset + limit) < total)

        # CursorPagination
        matched = apply_ordering_in_memory(matched, ordering_list, id_tiebreaker=True)
        start = 0
        if pagination.cursor is not None:
            cursor_values, cursor_id = decode_cursor(pagination.cursor)
            start = cursor_position(matched, cursor_values, cursor_id, ordering_list)

        page_items = matched[start : start + pagination.limit]
        has_next = (start + pagination.limit) < len(matched)
        next_cursor: str | None = None
        if page_items and has_next:
            last = page_items[-1]
            field_values = {
                ".".join(ob.field.path): get_field_value(last, ob.field.path)
                for ob in ordering_list
            }
            next_cursor = encode_cursor(field_values, last.get_id())

        return Page(items=page_items, total=None, has_next=has_next, next_cursor=next_cursor)

    def watch(self, *, include_existing: bool = False) -> Iterator[TPersistable]:
        q: queue.Queue[TPersistable] = queue.Queue()
        self._insert_queues.append(q)
        try:
            seen: set[Any] = set()
            if include_existing:
                for obj in list(self.session.values()):
                    seen.add(obj.get_id())
                    yield deepcopy(cast(TPersistable, obj))
            while True:
                try:
                    obj = q.get(timeout=_WATCH_POLL)
                except queue.Empty:
                    continue
                if obj.get_id() in seen:
                    seen.discard(obj.get_id())
                    continue
                yield obj
        finally:
            self._insert_queues.remove(q)

    def update(self, obj: TPersistable) -> None:
        key = obj.get_id()
        existing = cast(TPersistable | None, self.session.get(key))
        if existing is None:
            raise ResourceDoesNotExistError(obj)
        if existing.get_version() != obj.get_version():
            raise OptimisticLockError(obj)
        copy = deepcopy(obj)
        copy.set_version(obj.get_version() + 1)
        self._prepare_stored_copy(copy)
        self.session[key] = copy
        obj.set_version(obj.get_version() + 1)
        self._after_mutate(obj)

    def is_modified(self, obj: TPersistable) -> bool:
        stored = cast(TPersistable | None, self.session.get(obj.get_id()))
        if stored is None:
            raise ResourceDoesNotExistError(obj)
        return stored.get_version() != obj.get_version()

    def count(self, specification: ISpecification) -> int:
        return sum(1 for obj in self.session.values() if specification.is_satisfied(obj))

    def upsert(self, obj: TPersistable) -> None:
        key = obj.get_id()
        existing = cast(TPersistable | None, self.session.get(key))
        copy = deepcopy(obj)
        copy.set_version((existing.get_version() + 1) if existing is not None else 0)
        self._prepare_stored_copy(copy)
        self.session[key] = copy
        self._after_mutate(obj)


class InMemoryRepository(
    IAggregateRepository[TId, TAggregate, dict[Any, Persistable[Any]]],
    InMemoryPersistableRepository[TId, TAggregate],
):
    """In-memory ``IAggregateRepository`` for testing and prototyping.

    Extends ``InMemoryPersistableRepository`` with domain-event collection.
    The session dict is keyed by raw ``aggregate.id.value``.
    Session management (rollback support) is provided by ``InMemoryDBContext``.
    """

    def _prepare_stored_copy(self, copy: TAggregate) -> None:  # type: ignore[override]
        cast(Any, copy).clear_events()
