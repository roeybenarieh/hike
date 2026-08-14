from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, not_, or_, select, true
from sqlalchemy.orm import InstrumentedAttribute

from hike.ddd.entity import Entity, TerminalFieldProxy
from hike.ddd.specifications import ISpecificationVisitor
from hike.ddd.specifications.specs import (
    AndSpecification,
    BaseFilterSpecification,
    EqualSpecification,
    GreaterThanEqualSpecification,
    GreaterThanSpecification,
    LessThanEqualSpecification,
    LessThanSpecification,
    NotEqualSpecification,
    NotSpecification,
    OrSpecification,
)

_EMPTY_FILTER: ColumnElement[Any] = true()


class ISQLAlchemyMapper(ABC):
    """Bridge between domain entity classes and SQLAlchemy ORM constructs.

    Subclass this and pass an instance to ``SQLAlchemyRepository``.  Two concrete
    implementations are provided: ``DictSQLAlchemyMapper`` (relational, one ORM model
    per entity) and ``FlatSQLAlchemyMapper`` (embedded, all fields on the root model).

    Required methods:
    - ``get_model`` — entity class → ORM model class
    - ``get_column`` — (ORM model class, field name) → SQLAlchemy column

    Optional methods (override for the embedded strategy):
    - ``resolve_path`` — full field path → column (bypasses JOIN traversal)
    - ``expand_nested`` — serialize a single nested entity for the parent model dict
    - ``collect_nested`` — read a single nested entity from the parent ORM object
    - ``expand_list_nested`` — serialize a list[Entity] field (e.g. to JSON column)
    - ``collect_list_nested`` — read a list[Entity] field from the parent ORM object
    """

    @abstractmethod
    def get_model(self, entity_class: type[Entity[Any]]) -> type[Any]:
        """Return the SQLAlchemy mapped class for *entity_class*."""
        ...

    @abstractmethod
    def get_column(self, model_class: type, field_name: str) -> InstrumentedAttribute[Any]:
        """Return the SQLAlchemy column attribute for *field_name* on *model_class*."""
        ...

    def resolve_path(self, path: list[str]) -> InstrumentedAttribute[Any] | None:
        """Return the column for a full field path, bypassing JOIN traversal.

        ``None`` (default) → use standard model traversal.
        Override for embedded mapping: ``['engine', 'price']`` → ``root_model.engine_price``.
        """
        return None

    def expand_nested(self, entity: Any, entity_cls: type, field_name: str) -> dict[str, Any]:
        """Serialize a single nested ``Field[Entity]`` to column key-value pairs.

        ``{}`` (default) → repository uses ``session.merge()`` relational path.
        Override for embedded: return ``{'engine_id': ..., 'engine_name': ..., ...}``.
        """
        return {}

    def collect_nested(self, orm_obj: Any, entity_cls: type, field_name: str) -> dict[str, Any] | None:
        """Read raw field values for a nested entity from the parent ORM object.

        ``None`` (default) → repository reads the ORM relationship attribute.
        Override for embedded: read prefixed columns from *orm_obj* directly.
        """
        return None

    def expand_list_nested(
        self, entities: list[Any], entity_cls: type, field_name: str
    ) -> dict[str, Any] | None:
        """Serialize a ``list[Entity]`` field to column key-value pairs.

        ``None`` (default) → repository creates ORM model instances (relational cascade).
        Override for embedded: ``{field_name: [to_dict(e) for e in entities]}`` for a JSON column.
        """
        return None

    def collect_list_nested(
        self, orm_obj: Any, entity_cls: type, field_name: str
    ) -> list[dict[str, Any]] | None:
        """Read a ``list[Entity]`` field from the parent ORM object.

        ``None`` (default) → repository reads the ORM relationship list.
        Override for embedded: read and return the JSON column value.
        """
        return None


def _proxy_chain(proxy: TerminalFieldProxy) -> list[TerminalFieldProxy]:
    """Return the proxy chain ordered from root to leaf."""
    chain: list[TerminalFieldProxy] = []
    node: TerminalFieldProxy | None = proxy
    while node is not None:
        chain.append(node)
        node = node.parent
    chain.reverse()
    return chain


class SQLAlchemyEvaluationSpecificationVisitor(ISpecificationVisitor):
    """Translates a specification tree into a SQLAlchemy SELECT with WHERE filters.

    Uses the aggregate root class and an :class:`ISQLAlchemyMapper` to resolve
    field paths — no SQLAlchemy ORM introspection required.

    Path traversal rules (driven by the ``FieldProxy`` chain):

    - **Intermediate steps** whose ``field_type`` is an :class:`~hike.ddd.entity.Entity`
      subclass are treated as foreign-key relationships.  The mapper is asked for the
      related model class and a JOIN is accumulated.
    - **The leaf step** whose ``field_type`` is a ValueObject is resolved to a
      SQLAlchemy column via the mapper.

    Example::

        mapper = MyMapper()
        visitor = SQLAlchemyEvaluationSpecificationVisitor(Boat, mapper)
        (Boat.engine.price > 1_000).accept(visitor)
        stmt = visitor.result()
        # → SELECT boat.* FROM boat JOIN engine ON ... WHERE engine.price > 1000
    """

    def __init__(
        self,
        aggregate_class: type[Entity[Any]],
        mapper: ISQLAlchemyMapper,
    ) -> None:
        self._mapper = mapper
        self._root_model = mapper.get_model(aggregate_class)
        self.filters: ColumnElement[Any] = _EMPTY_FILTER
        self._joins: list[type] = []
        self._joined_models: set[type] = set()

    def result(self) -> Select[Any]:
        stmt = select(self._root_model)
        for join_target in self._joins:
            stmt = stmt.join(join_target)
        return stmt.filter(self.filters)

    def _pop_filters(self) -> ColumnElement[Any]:
        filters = self.filters
        self.filters = _EMPTY_FILTER
        return filters

    def _visit_left_right_spec(
        self, spec: AndSpecification | OrSpecification
    ) -> tuple[ColumnElement[Any], ColumnElement[Any]]:
        spec.left.accept(self)
        left = self._pop_filters()
        spec.right.accept(self)
        return left, self._pop_filters()

    def resolve_column(self, proxy: TerminalFieldProxy) -> InstrumentedAttribute[Any]:
        """Walk the FieldProxy chain and return the SQLAlchemy column for the leaf field.

        First checks ``mapper.resolve_path`` (used by embedded mappers that keep all fields
        on the root model).  Falls back to the standard JOIN-traversal when it returns ``None``.
        """
        chain = _proxy_chain(proxy)
        path = [step.field_name for step in chain]
        col = self._mapper.resolve_path(path)
        if col is not None:
            return col

        current_model = self._root_model

        for i, step in enumerate(chain):
            is_leaf = i == len(chain) - 1
            if is_leaf:
                return self._mapper.get_column(current_model, step.field_name)
            # Intermediate step: field_type must be an Entity — resolve its model and JOIN.
            related_model = self._mapper.get_model(step.field_type)  # pyright: ignore[reportArgumentType]
            if related_model not in self._joined_models:
                self._joins.append(related_model)
                self._joined_models.add(related_model)
            current_model = related_model

        raise ValueError(f"Empty FieldProxy path for {proxy!r}")  # pragma: no cover

    def _visit_filter(
        self,
        spec: BaseFilterSpecification,
    ) -> tuple[InstrumentedAttribute[Any], Any]:
        return self.resolve_column(spec.field), spec.operand

    def visit_and(self, spec: AndSpecification) -> None:
        left, right = self._visit_left_right_spec(spec)
        self.filters = and_(left, right)

    def visit_or(self, spec: OrSpecification) -> None:
        left, right = self._visit_left_right_spec(spec)
        self.filters = or_(left, right)

    def visit_not(self, spec: NotSpecification) -> None:
        spec.spec.accept(self)
        self.filters = not_(self.filters)

    def visit_equal(self, spec: EqualSpecification) -> None:
        col, val = self._visit_filter(spec)
        self.filters = col == val

    def visit_not_equal(self, spec: NotEqualSpecification) -> None:
        col, val = self._visit_filter(spec)
        self.filters = col != val

    def visit_greater_than(self, spec: GreaterThanSpecification) -> None:
        col, val = self._visit_filter(spec)
        self.filters = col > val

    def visit_greater_than_equal(self, spec: GreaterThanEqualSpecification) -> None:
        col, val = self._visit_filter(spec)
        self.filters = col >= val

    def visit_less_than(self, spec: LessThanSpecification) -> None:
        col, val = self._visit_filter(spec)
        self.filters = col < val

    def visit_less_than_equal(self, spec: LessThanEqualSpecification) -> None:
        col, val = self._visit_filter(spec)
        self.filters = col <= val
