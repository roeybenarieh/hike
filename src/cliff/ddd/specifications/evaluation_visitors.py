from __future__ import annotations

from typing import Any

from redis import Redis
from sqlalchemy import ColumnElement, Select, select, true, and_, or_, not_
from sqlalchemy.orm import QueryableAttribute

from cliff.ddd.specifications import (
    IVisitor,
    AndSpecification, OrSpecification, NotSpecification,
    EqualSpecification, NotEqualSpecification,
    GreaterThanSpecification, GreaterThanEqualSpecification,
    LessThanSpecification, LessThanEqualSpecification,
)
from cliff.ddd.specifications.specs import BaseLeftRightSpecification, BaseFilterSpecification
from cliff.ddd.value_object import ValueObject

_EMPTY_FILTER: ColumnElement[Any] = true()


# ---------------------------------------------------------------------------
# In-memory visitor
# ---------------------------------------------------------------------------


class InMemoryEvaluationVisitor(IVisitor):
    """Evaluates a specification tree against a single in-memory object.

    Usage::

        visitor = InMemoryEvaluationVisitor(my_entity)
        spec.accept(visitor)
        if visitor.result:
            ...

    The visitor reads fields via ``getattr`` and unwraps ``ValueObject``
    instances to their ``.value`` automatically.
    """

    def __init__(self, obj: object) -> None:
        self._obj = obj
        self.result: bool = True

    def _field_value(self, spec: BaseFilterSpecification) -> object:
        val = getattr(self._obj, spec.field.field_name)
        return val.value if isinstance(val, ValueObject) else val  # type: ignore[union-attr]

    def _pop_result(self) -> bool:
        r = self.result
        self.result = False
        return r

    # --- composites ---

    def visit_and(self, spec: AndSpecification) -> None:
        spec.left.accept(self)
        left = self._pop_result()
        spec.right.accept(self)
        self.result = left and self.result

    def visit_or(self, spec: OrSpecification) -> None:
        spec.left.accept(self)
        left = self._pop_result()
        spec.right.accept(self)
        self.result = left or self.result

    def visit_not(self, spec: NotSpecification) -> None:
        spec.spec.accept(self)
        self.result = not self.result

    # --- leaves ---

    def visit_equal(self, spec: EqualSpecification) -> None:
        self.result = self._field_value(spec) == spec.operand

    def visit_not_equal(self, spec: NotEqualSpecification) -> None:
        self.result = self._field_value(spec) != spec.operand

    def visit_greater_than(self, spec: GreaterThanSpecification) -> None:
        self.result = self._field_value(spec) > spec.operand  # type: ignore[operator]

    def visit_greater_than_equal(self, spec: GreaterThanEqualSpecification) -> None:
        self.result = self._field_value(spec) >= spec.operand  # type: ignore[operator]

    def visit_less_than(self, spec: LessThanSpecification) -> None:
        self.result = self._field_value(spec) < spec.operand  # type: ignore[operator]

    def visit_less_than_equal(self, spec: LessThanEqualSpecification) -> None:
        self.result = self._field_value(spec) <= spec.operand  # type: ignore[operator]


# ---------------------------------------------------------------------------
# SQLAlchemy visitor
# ---------------------------------------------------------------------------


class SQLAlchemyEvaluationVisitor(IVisitor):
    """Translates a specification tree into a SQLAlchemy SELECT with WHERE filters."""

    def __init__(self, table_object: type[QueryableAttribute[Any]]) -> None:
        self.table_object = table_object
        self.filters: ColumnElement[Any] = _EMPTY_FILTER

    def result(self) -> Select[Any]:
        result = select(self.table_object).filter(self.filters)
        return result

    def _pop_filters(self) -> ColumnElement[Any]:
        filters = self.filters
        self.filters = _EMPTY_FILTER
        return filters

    def _visit_left_right_spec(self, spec: BaseLeftRightSpecification) -> tuple[ColumnElement[Any], ColumnElement[Any]]:
        spec.left.accept(self)
        left = self._pop_filters()
        spec.right.accept(self)
        return left, self._pop_filters()

    def _visit_filter(self, spec: BaseFilterSpecification) -> tuple[QueryableAttribute[Any], Any]:
        return getattr(self.table_object, spec.field.field_name), spec.operand

    # --- composites ---

    def visit_and(self, spec: AndSpecification) -> None:
        left, right = self._visit_left_right_spec(spec)
        self.filters = and_(left, right)

    def visit_or(self, spec: OrSpecification) -> None:
        left, right = self._visit_left_right_spec(spec)
        self.filters = or_(left, right)

    def visit_not(self, spec: NotSpecification) -> None:
        spec.spec.accept(self)
        self.filters = not_(self.filters)

    # --- leaves ---

    def visit_equal(self, spec: EqualSpecification) -> None:
        col, val = self._visit_filter(spec)
        self.filters = col == val  # type: ignore[assignment]

    def visit_not_equal(self, spec: NotEqualSpecification) -> None:
        col, val = self._visit_filter(spec)
        self.filters = col != val  # type: ignore[assignment]

    def visit_greater_than(self, spec: GreaterThanSpecification) -> None:
        col, val = self._visit_filter(spec)
        self.filters = col > val  # type: ignore[assignment]

    def visit_greater_than_equal(self, spec: GreaterThanEqualSpecification) -> None:
        col, val = self._visit_filter(spec)
        self.filters = col >= val  # type: ignore[assignment]

    def visit_less_than(self, spec: LessThanSpecification) -> None:
        col, val = self._visit_filter(spec)
        self.filters = col < val  # type: ignore[assignment]

    def visit_less_than_equal(self, spec: LessThanEqualSpecification) -> None:
        col, val = self._visit_filter(spec)
        self.filters = col <= val  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# MongoDB visitor
# ---------------------------------------------------------------------------


class MongoDBEvaluationVisitor(IVisitor):
    """Translates a specification tree into a pymongo filter dict.

    Usage::

        visitor = MongoDBEvaluationVisitor()
        spec.accept(visitor)
        documents = collection.find(visitor.filters)

    The filter dict is available as ``visitor.filters`` to pass directly to
    any pymongo collection method.
    """

    def __init__(self) -> None:
        self.filters: dict[str, Any] = {}  # empty dict matches every document

    def _pop_filters(self) -> dict[str, Any]:
        f = self.filters
        self.filters = {}
        return f

    def _visit_left_right_spec(
            self, spec: BaseLeftRightSpecification
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        spec.left.accept(self)
        left = self._pop_filters()
        spec.right.accept(self)
        return left, self._pop_filters()

    # --- composites ---

    def visit_and(self, spec: AndSpecification) -> None:
        left, right = self._visit_left_right_spec(spec)
        self.filters = {"$and": [left, right]}

    def visit_or(self, spec: OrSpecification) -> None:
        left, right = self._visit_left_right_spec(spec)
        self.filters = {"$or": [left, right]}

    def visit_not(self, spec: NotSpecification) -> None:
        spec.spec.accept(self)
        inner = self._pop_filters()
        # $nor is the standard way to negate an entire filter expression
        self.filters = {"$nor": [inner]}

    # --- leaves ---

    def visit_equal(self, spec: EqualSpecification) -> None:
        self.filters = {spec.field.field_name: {"$eq": spec.operand}}

    def visit_not_equal(self, spec: NotEqualSpecification) -> None:
        self.filters = {spec.field.field_name: {"$ne": spec.operand}}

    def visit_greater_than(self, spec: GreaterThanSpecification) -> None:
        self.filters = {spec.field.field_name: {"$gt": spec.operand}}

    def visit_greater_than_equal(self, spec: GreaterThanEqualSpecification) -> None:
        self.filters = {spec.field.field_name: {"$gte": spec.operand}}

    def visit_less_than(self, spec: LessThanSpecification) -> None:
        self.filters = {spec.field.field_name: {"$lt": spec.operand}}

    def visit_less_than_equal(self, spec: LessThanEqualSpecification) -> None:
        self.filters = {spec.field.field_name: {"$lte": spec.operand}}


# ---------------------------------------------------------------------------
# Redis / RediSearch visitor
# ---------------------------------------------------------------------------

# RediSearch query syntax quick reference:
#   Numeric range : @field:[min max]      (inclusive bounds)
#                   @field:[(min max]     ( = exclusive lower bound
#   Tag exact match: @field:{value}
#   AND            : expr1 expr2          (space-separated = implicit AND)
#   OR             : (expr1 | expr2)
#   NOT            : -(expr)


def _redis_operand(operand: object) -> str:
    """Format an operand for embedding in a RediSearch query string."""
    if isinstance(operand, str):
        # Escape special RediSearch characters inside tag values
        escaped = operand.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        return escaped
    return str(operand)


def _redis_leaf(field_name: str, operand: object, op: str) -> str:
    """Build a RediSearch clause for a single field comparison."""
    if isinstance(operand, (int, float)):
        val = float(operand)
        match op:
            case "eq":
                return f"@{field_name}:[{val} {val}]"
            case "ne":
                return f"-(@{field_name}:[{val} {val}])"
            case "gt":
                return f"@{field_name}:[({val} +inf]"
            case "gte":
                return f"@{field_name}:[{val} +inf]"
            case "lt":
                return f"@{field_name}:[-inf ({val}]"
            case "lte":
                return f"@{field_name}:[-inf {val}]"
            case _:
                raise ValueError(f"Unknown operator: {op!r}")
    else:
        tag = _redis_operand(operand)
        match op:
            case "eq":
                return f"@{field_name}:{{{tag}}}"
            case "ne":
                return f"-(@{field_name}:{{{tag}}})"
            # Range operators on text fields are not meaningful in RediSearch;
            # raise clearly rather than silently producing a bad query.
            case _:
                raise TypeError(
                    f"RediSearch range operator '{op}' is only supported for "
                    f"numeric fields; got operand {operand!r} of type {type(operand).__name__}"
                )


class RedisEvaluationVisitor(IVisitor):
    """Translates a specification tree into a RediSearch FT.SEARCH query string.

    Usage::

        visitor = RedisEvaluationVisitor(redis_client, index_name="idx:products")
        spec.accept(visitor)
        raw_results = visitor.result()   # calls FT.SEARCH and returns the raw reply

    The query string is also accessible as ``visitor.query`` before calling
    ``result()`` if you need to inspect or log it first.

    Field mapping
    -------------
    Numeric fields use RediSearch range syntax (``@price:[50 +inf]``).
    String fields use tag syntax (``@category:{electronics}``).  The visitor
    infers which to use from the Python type of the operand at visit time.
    """

    _MATCH_ALL = "*"

    def __init__(self, client: Redis, index_name: str) -> None:
        self._client = client
        self._index = index_name
        self.query: str = self._MATCH_ALL

    def result(self) -> Any:
        return self._client.ft(self._index).search(self.query)  # type: ignore[no-any-return]

    def _pop_query(self) -> str:
        q = self.query
        self.query = self._MATCH_ALL
        return q

    def _visit_left_right_spec(self, spec: BaseLeftRightSpecification) -> tuple[str, str]:
        spec.left.accept(self)
        left = self._pop_query()
        spec.right.accept(self)
        return left, self._pop_query()

    # --- composites ---

    def visit_and(self, spec: AndSpecification) -> None:
        left, right = self._visit_left_right_spec(spec)
        # Implicit AND in RediSearch: space-separate the clauses
        self.query = f"({left}) ({right})"

    def visit_or(self, spec: OrSpecification) -> None:
        left, right = self._visit_left_right_spec(spec)
        self.query = f"({left} | {right})"

    def visit_not(self, spec: NotSpecification) -> None:
        spec.spec.accept(self)
        self.query = f"-({self._pop_query()})"

    # --- leaves ---

    def visit_equal(self, spec: EqualSpecification) -> None:
        self.query = _redis_leaf(spec.field.field_name, spec.operand, "eq")

    def visit_not_equal(self, spec: NotEqualSpecification) -> None:
        self.query = _redis_leaf(spec.field.field_name, spec.operand, "ne")

    def visit_greater_than(self, spec: GreaterThanSpecification) -> None:
        self.query = _redis_leaf(spec.field.field_name, spec.operand, "gt")

    def visit_greater_than_equal(self, spec: GreaterThanEqualSpecification) -> None:
        self.query = _redis_leaf(spec.field.field_name, spec.operand, "gte")

    def visit_less_than(self, spec: LessThanSpecification) -> None:
        self.query = _redis_leaf(spec.field.field_name, spec.operand, "lt")

    def visit_less_than_equal(self, spec: LessThanEqualSpecification) -> None:
        self.query = _redis_leaf(spec.field.field_name, spec.operand, "lte")
