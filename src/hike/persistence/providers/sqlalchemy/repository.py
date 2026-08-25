from __future__ import annotations

import time
import uuid as _uuid
from collections.abc import Iterator, Sequence
from typing import Any, cast, get_origin, get_type_hints

from sqlalchemy import ColumnElement, and_, asc as sa_asc, desc as sa_desc, func, or_, select, text
from sqlalchemy.orm import InstrumentedAttribute, Session

from hike.entity import Entity, Field
from hike.persistence.ordering import OrderBy
from hike.persistence.pagination import (
    OffsetPagination,
    Page,
    PagePagination,
    Pagination,
    decode_cursor,
    encode_cursor,
)
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

from .mappers import DictAutoSQLAlchemyMapper, VERSION_ATTR
from .visitor import ISQLAlchemyMapper, SQLAlchemyEvaluationSpecificationVisitor


def _drain_notifications(raw: Any, timeout: float = 1.0) -> list[str]:
    """Return payload strings of all pending database notifications.

    Blocks up to *timeout* seconds if the queue is empty.
    Supports psycopg3 (``connection.notifies()`` generator) and psycopg2
    (``connection.notifies`` list, drained via ``select`` + ``poll``).
    """
    payloads: list[str] = []
    if callable(getattr(raw, "notifies", None)):
        # psycopg3: generator exits after timeout seconds with no events
        for notification in raw.notifies(timeout=timeout):  # pyright: ignore[reportAny]
            payloads.append(notification.payload)  # pyright: ignore[reportAny]
    else:
        # psycopg2: select + poll
        import select as _select
        _select.select([raw], [], [], timeout)
        raw.poll()  # pyright: ignore[reportAttributeAccessIssue]
        for notification in list(raw.notifies):  # pyright: ignore[reportAttributeAccessIssue]
            payloads.append(notification.payload)  # pyright: ignore[reportAny]
        raw.notifies.clear()  # pyright: ignore[reportAttributeAccessIssue]
    return payloads


def _coerce_pk(payload: str) -> Any:
    """Parse a NOTIFY payload string back to a Python primary-key value (UUID, int, or str)."""
    try:
        return _uuid.UUID(payload)
    except ValueError:
        pass
    try:
        return int(payload)
    except ValueError:
        pass
    return payload


def _build_sa_keyset_filter(
    col_pairs: list[tuple[InstrumentedAttribute[Any], OrderBy]],
    cursor_values: dict[str, Any],
    cursor_id: Any,
    id_col: InstrumentedAttribute[Any],
) -> ColumnElement[Any]:
    """Build a multi-column keyset WHERE clause for cursor pagination.

    For ordering ``(c1 ASC, c2 DESC)`` with cursor values ``(v1, v2, vid)``:

        (c1 > v1)
        OR (c1 = v1 AND c2 < v2)
        OR (c1 = v1 AND c2 = v2 AND id > vid)

    The final clause uses ``id`` as a tiebreaker so pages are stable when
    all explicit ordering fields are equal.
    """
    clauses: list[ColumnElement[Any]] = []
    for i, (col_i, ob_i) in enumerate(col_pairs):
        val_i = cursor_values[".".join(ob_i.field.path)]
        prefix = [
            col_pairs[j][0] == cursor_values[".".join(col_pairs[j][1].field.path)]
            for j in range(i)
        ]
        gt_lt: ColumnElement[Any] = col_i > val_i if ob_i.direction == "asc" else col_i < val_i
        clauses.append(and_(*prefix, gt_lt))

    # Final clause: all ordering fields equal AND id > cursor_id
    all_eq = [col == cursor_values[".".join(ob.field.path)] for col, ob in col_pairs]
    clauses.append(and_(*all_eq, id_col > cursor_id))
    return or_(*clauses)


class SQLAlchemyPersistableRepository(IRepository[TId, TPersistable, Session]):
    """Generic SQLAlchemy ORM repository for any ``Persistable`` object.

    ``aggregate_class`` is the domain class to persist.  ``mapper`` bridges
    domain entities and SQLAlchemy ORM models.  When omitted, a
    ``DictAutoSQLAlchemyMapper`` is created automatically — Hike infers the full
    ORM schema from the class's field annotations.

    Four concrete mapper implementations are available:
    - ``DictAutoSQLAlchemyMapper`` (default): relational schema inferred from annotations.
    - ``FlatAutoSQLAlchemyMapper``: flat single-table schema inferred from annotations.
    - ``DictSQLAlchemyMapper``: relational, one ORM model per entity class.
    - ``FlatSQLAlchemyMapper``: embedded, all fields on a single root model.

    Supported object shapes:
    - Flat: all ``Field[ValueObject]`` fields map to columns on the root ORM model.
    - ``list[Entity]``: child entities stored in a related table (cascade) or a JSON
      column (embedded mapper).
    - ``Field[Entity]``: single nested entity, handled recursively at any depth.

    Install with: ``pip install hike[sqlalchemy]``
    """

    def __init__(
        self,
        aggregate_class: type[TPersistable],
        mapper: ISQLAlchemyMapper | None = None,
    ) -> None:
        super().__init__()
        self._aggregate_class = aggregate_class
        if mapper is None:
            mapper = DictAutoSQLAlchemyMapper(aggregate_class)  # type: ignore[arg-type]
        self._mapper = mapper
        self._model_class = mapper.get_model(aggregate_class)  # type: ignore[arg-type]
        self._listen_notify_ok: bool | None = None

    @property
    def _listen_channel(self) -> str:
        return f"hike_{self._model_class.__tablename__}_inserts"  # pyright: ignore[reportUnknownMemberType]

    def _probe_listen_notify(self) -> bool:
        """Return True if the database supports LISTEN/NOTIFY semantics.

        Known dialects are resolved immediately; unknown dialects are probed once
        with a real LISTEN/UNLISTEN call — result is cached for this instance.
        """
        if self._listen_notify_ok is None:
            dialect = self.session.connection().dialect.name
            if dialect in {"postgresql"}:
                self._listen_notify_ok = True
            elif dialect in {"sqlite", "mysql", "mariadb", "mssql", "oracle"}:
                self._listen_notify_ok = False
            else:
                try:
                    engine = self.session.connection().engine
                    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as probe:
                        probe.execute(text(f"LISTEN {self._listen_channel}"))
                        probe.execute(text(f"UNLISTEN {self._listen_channel}"))
                    self._listen_notify_ok = True
                except Exception:
                    self._listen_notify_ok = False
        return self._listen_notify_ok

    def _hints(self) -> dict[str, Any]:
        try:
            return get_type_hints(self._aggregate_class)
        except Exception:
            return {}

    def _list_entity_fields(self, hints: dict[str, Any]) -> dict[str, type]:
        """Return {field_name: elem_cls} for list[Entity] fields."""
        result: dict[str, type] = {}
        for name, ann in hints.items():
            if get_origin(ann) is not list:
                continue
            args: tuple[Any, ...] = getattr(ann, "__args__", ())
            elem_cls = args[0] if args else None
            if isinstance(elem_cls, type) and issubclass(elem_cls, Entity):
                result[name] = elem_cls
        return result

    def _single_entity_fields(self, hints: dict[str, Any]) -> dict[str, type]:
        """Return {field_name: entity_cls} for Field[Entity] (non-list) fields."""
        result: dict[str, type] = {}
        for name, ann in hints.items():
            if get_origin(ann) is not Field:
                continue
            args: tuple[Any, ...] = getattr(ann, "__args__", ())
            inner = args[0] if args else None
            bare = inner if isinstance(inner, type) else get_origin(inner)
            if isinstance(bare, type) and issubclass(bare, Entity):
                result[name] = bare
        return result

    def _sync_entity(self, entity: Any, entity_cls: type) -> Any:
        """Recursively upsert a nested entity's ORM row (depth-first) and return the tracked model."""
        try:
            hints: dict[str, Any] = get_type_hints(entity_cls)
        except Exception:
            hints = {}
        sub_single = self._single_entity_fields(hints)
        model_cls = self._mapper.get_model(entity_cls)
        entity_dict: dict[str, Any] = entity.to_dict()

        for sub_name, sub_cls in sub_single.items():
            sub_entity: Any = getattr(entity, sub_name, None)
            if sub_entity is None:
                continue
            expanded = self._mapper.expand_nested(sub_entity, sub_cls, sub_name)
            if expanded:
                entity_dict.pop(sub_name, None)
                entity_dict.update(expanded)
            else:
                entity_dict[sub_name] = self._sync_entity(sub_entity, sub_cls)

        return self.session.merge(model_cls(**entity_dict))

    def _reconstruct_entity_dict(self, orm_obj: Any, entity_cls: type) -> dict[str, Any]:
        """Recursively read raw field values from *orm_obj* for reconstructing *entity_cls*."""
        try:
            hints: dict[str, Any] = get_type_hints(entity_cls)
        except Exception:
            hints = {}
        sub_single = self._single_entity_fields(hints)
        init_names = set(cast(type[Entity[Any]], entity_cls).get_init_field_names())
        result: dict[str, Any] = {}
        for k in init_names:
            if not hasattr(orm_obj, k):
                continue
            v: Any = getattr(orm_obj, k)
            if k in sub_single and v is not None:
                sub_dict = self._mapper.collect_nested(orm_obj, sub_single[k], k)
                result[k] = cast(type[Entity[Any]], sub_single[k]).from_dict(
                    sub_dict if sub_dict is not None else self._reconstruct_entity_dict(v, sub_single[k]),
                )
            else:
                result[k] = v
        return result

    def _to_model_dict(self, obj: TPersistable) -> dict[str, Any]:
        """Serialize *obj* to a dict suitable for constructing an ORM model."""
        hints = self._hints()
        list_fields = self._list_entity_fields(hints)
        single_fields = self._single_entity_fields(hints)
        flat = obj.to_dict()

        for name, elem_cls in list_fields.items():
            if name not in flat:
                continue
            entities: list[Any] = getattr(obj, name)
            expanded = self._mapper.expand_list_nested(entities, elem_cls, name)
            if expanded is not None:
                flat.update(expanded)
            else:
                nested_model_cls = self._mapper.get_model(elem_cls)
                flat[name] = [nested_model_cls(**item) for item in cast(list[Any], flat[name])]

        for name, entity_cls in single_fields.items():
            nested_entity: Any = getattr(obj, name, None)
            if nested_entity is None:
                continue
            expanded = self._mapper.expand_nested(nested_entity, entity_cls, name)
            if expanded:
                flat.pop(name, None)
                flat.update(expanded)
            else:
                flat[name] = self._sync_entity(nested_entity, entity_cls)

        return flat

    def _from_model(self, model: Any) -> TPersistable:
        hints = self._hints()
        list_fields = self._list_entity_fields(hints)
        single_fields = self._single_entity_fields(hints)
        init_names = set(self._aggregate_class.get_init_field_names())
        data: dict[str, Any] = {}
        for name in init_names:
            if name in list_fields:
                elem_cls = list_fields[name]
                json_list = self._mapper.collect_list_nested(model, elem_cls, name)
                if json_list is not None:
                    data[name] = [cast(type[Entity[Any]], elem_cls).from_dict(item) for item in json_list]
                elif hasattr(model, name):
                    val: Any = getattr(model, name)
                    elem_init_names = set(cast(type[Entity[Any]], elem_cls).get_init_field_names())
                    data[name] = [
                        cast(type[Entity[Any]], elem_cls).from_dict({k: getattr(item, k) for k in elem_init_names if hasattr(item, k)})
                        for item in cast(list[Any], val)
                    ]
            elif name in single_fields:
                entity_cls = single_fields[name]
                raw = self._mapper.collect_nested(model, entity_cls, name)
                if raw is not None:
                    data[name] = cast(type[Entity[Any]], entity_cls).from_dict(raw)
                elif hasattr(model, name):
                    val = getattr(model, name)
                    if val is not None:
                        data[name] = cast(type[Entity[Any]], entity_cls).from_dict(self._reconstruct_entity_dict(val, entity_cls))
            elif hasattr(model, name):
                data[name] = getattr(model, name)
        obj = self._aggregate_class(**data)
        obj.set_version(getattr(model, VERSION_ATTR))
        return obj

    def save(self, obj: TPersistable) -> TId:
        model = self._model_class(**self._to_model_dict(obj))
        try:
            self.session.add(model)
            self.session.flush()
        except Exception as exc:
            raise ResourceAlreadyExistError(obj) from exc
        if self._probe_listen_notify():
            self.session.execute(
                text("SELECT pg_notify(:ch, :pk)"),
                {"ch": self._listen_channel, "pk": str(getattr(model, "id"))},
            )
        obj.set_version(getattr(model, VERSION_ATTR))
        self._after_mutate(obj)
        return obj.get_id()  # pyright: ignore[reportReturnType]

    def _delete(self, identifier: TId) -> None:
        model = self.session.get(self._model_class, identifier)
        if model is None:
            raise ResourceDoesNotExistError(identifier)
        self.session.delete(model)

    def get_one(self, identifier: TId) -> TPersistable:
        model = self.session.get(self._model_class, identifier)
        if model is None:
            raise ResourceDoesNotExistError(identifier)
        return self._from_model(model)

    def _get_many(
        self,
        specification: ISpecification,
        *,
        ordering: Sequence[OrderBy] | None = None,
        pagination: Pagination | None = None,
    ) -> list[TPersistable] | Page[TPersistable]:
        visitor = SQLAlchemyEvaluationSpecificationVisitor(self._aggregate_class, self._mapper)  # type: ignore[arg-type]
        specification.accept(visitor)

        ordering_list = list(ordering) if ordering else []
        col_pairs: list[tuple[InstrumentedAttribute[Any], OrderBy]] = [
            (visitor.resolve_column(ob.field), ob) for ob in ordering_list
        ]
        stmt = visitor.result()
        for col, ob in col_pairs:
            stmt = stmt.order_by(sa_asc(col) if ob.direction == "asc" else sa_desc(col))

        if pagination is None:
            rows = self.session.scalars(stmt).all()
            return [self._from_model(row) for row in rows]

        id_col = self._mapper.get_column(self._model_class, "id")

        if isinstance(pagination, OffsetPagination):
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total: int = self.session.scalar(count_stmt) or 0
            rows = self.session.scalars(stmt.offset(pagination.offset).limit(pagination.limit)).all()
            return Page(
                items=[self._from_model(r) for r in rows],
                total=total,
                has_next=(pagination.offset + pagination.limit) < total,
            )

        if isinstance(pagination, PagePagination):
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = self.session.scalar(count_stmt) or 0
            offset = pagination.offset
            rows = self.session.scalars(stmt.offset(offset).limit(pagination.page_size)).all()
            return Page(
                items=[self._from_model(r) for r in rows],
                total=total,
                has_next=(offset + pagination.page_size) < total,
            )

        # CursorPagination — keyset WHERE + implicit id ORDER BY + fetch limit+1
        stmt = stmt.order_by(sa_asc(id_col))
        if pagination.cursor is not None:
            cursor_values, cursor_id = decode_cursor(pagination.cursor)
            stmt = stmt.where(_build_sa_keyset_filter(col_pairs, cursor_values, cursor_id, id_col))

        rows_list = list(self.session.scalars(stmt.limit(pagination.limit + 1)).all())
        has_next = len(rows_list) > pagination.limit
        page_rows = rows_list[: pagination.limit]
        page_items = [self._from_model(r) for r in page_rows]

        next_cursor: str | None = None
        if has_next and page_rows:
            last_model = page_rows[-1]
            field_values: dict[str, Any] = {
                ".".join(ob.field.path): getattr(last_model, ob.field.path[-1])
                for _, ob in col_pairs
            }
            next_cursor = encode_cursor(field_values, getattr(last_model, "id"))

        return Page(items=page_items, total=None, has_next=has_next, next_cursor=next_cursor)

    def is_modified(self, obj: TPersistable) -> bool:
        model = self.session.get(self._model_class, obj.get_id())
        if model is None:
            raise ResourceDoesNotExistError(obj)
        return getattr(model, VERSION_ATTR) != obj.get_version()

    def count(self, specification: ISpecification) -> int:
        visitor = SQLAlchemyEvaluationSpecificationVisitor(self._aggregate_class, self._mapper)  # type: ignore[arg-type]
        specification.accept(visitor)
        stmt = visitor.result()
        count_stmt = select(func.count()).select_from(stmt.subquery())
        return self.session.scalar(count_stmt) or 0

    def update(self, obj: TPersistable) -> None:
        model = self.session.get(self._model_class, obj.get_id())
        if model is None:
            raise ResourceDoesNotExistError(obj)
        if getattr(model, VERSION_ATTR) != obj.get_version():
            raise OptimisticLockError(obj)
        for key, val in self._to_model_dict(obj).items():
            setattr(model, key, val)
        setattr(model, VERSION_ATTR, getattr(model, VERSION_ATTR) + 1)
        obj.set_version(obj.get_version() + 1)
        self._after_mutate(obj)

    def upsert(self, obj: TPersistable) -> None:
        model = self.session.get(self._model_class, obj.get_id())
        if model is None:
            self.session.add(self._model_class(**self._to_model_dict(obj)))
        else:
            for key, val in self._to_model_dict(obj).items():
                setattr(model, key, val)
            setattr(model, VERSION_ATTR, getattr(model, VERSION_ATTR) + 1)
        self._after_mutate(obj)

    def watch(self, *, include_existing: bool = False) -> Iterator[TPersistable]:
        if self._probe_listen_notify():
            yield from self._watch_listen(include_existing=include_existing)
        else:
            seen_ids: set[Any] = set()
            for m in self.session.scalars(select(self._model_class)).all():
                obj_id = getattr(m, "id")
                if include_existing:
                    yield self._from_model(m)
                seen_ids.add(obj_id)
            yield from self._watch_poll(seen_ids)

    def _watch_poll(self, seen_ids: set[Any]) -> Iterator[TPersistable]:
        while True:
            time.sleep(0.2)
            new_items: list[TPersistable] = []
            for m in self.session.scalars(select(self._model_class)).all():
                obj_id = getattr(m, "id")
                if obj_id not in seen_ids:
                    seen_ids.add(obj_id)
                    new_items.append(self._from_model(m))
            for item in new_items:
                yield item

    def _watch_listen(self, *, include_existing: bool = False) -> Iterator[TPersistable]:
        """Database-native notification watch (PostgreSQL, CockroachDB, etc.).

        save() embeds the inserted PK as the NOTIFY payload; the watcher fetches
        exactly that row — no full-table scan, no deduplication state.
        """
        engine = self.session.connection().engine
        channel = self._listen_channel
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as listen_conn:
            listen_conn.execute(text(f"LISTEN {channel}"))
            raw: Any = cast(Any, listen_conn.connection).driver_connection
            seen: set[Any] = set()
            if include_existing:
                for m in self.session.scalars(select(self._model_class)).all():
                    pk = getattr(m, "id")
                    seen.add(pk)
                    yield self._from_model(m)
            while True:
                for payload in _drain_notifications(raw):
                    pk = _coerce_pk(payload)
                    if pk in seen:
                        seen.discard(pk)
                        continue
                    model = self.session.get(self._model_class, pk)
                    if model is not None:
                        yield self._from_model(model)


class SQLAlchemyRepository(
    IAggregateRepository[TId, TAggregate, Session],
    SQLAlchemyPersistableRepository[TId, TAggregate],
):
    """``IAggregateRepository`` backed by SQLAlchemy ORM.

    Extends ``SQLAlchemyPersistableRepository`` with domain-event collection.
    """
