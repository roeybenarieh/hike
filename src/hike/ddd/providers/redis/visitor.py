from __future__ import annotations

from typing import Any

from redis import Redis

from hike.ddd.specifications import ISpecificationVisitor
from hike.ddd.specifications.specs import (
    AndSpecification,
    BaseLeftRightSpecification,
    EqualSpecification,
    GreaterThanEqualSpecification,
    GreaterThanSpecification,
    LessThanEqualSpecification,
    LessThanSpecification,
    NotEqualSpecification,
    NotSpecification,
    OrSpecification,
)

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
            case _:
                raise TypeError(
                    f"RediSearch range operator '{op}' is only supported for "
                    f"numeric fields; got operand {operand!r} of type {type(operand).__name__}"
                )


class RedisEvaluationSpecificationVisitor(ISpecificationVisitor):
    """Translates a specification tree into a RediSearch FT.SEARCH query string.

    Usage::

        visitor = RedisEvaluationVisitor(redis_client, index_name="idx:products")
        spec.accept(visitor)
        raw_results = visitor.result()   # calls FT.SEARCH and returns the raw reply

    The query string is also accessible as ``visitor.query`` before calling
    ``result()`` if you need to inspect or log it first.
    """

    _MATCH_ALL = "*"

    def __init__(self, client: Redis, index_name: str) -> None:
        self._client = client
        self._index = index_name
        self.query: str = self._MATCH_ALL

    def result(self) -> Any:
        return self._client.ft(self._index).search(self.query)  # pyright: ignore[reportUnknownVariableType]

    def _pop_query(self) -> str:
        q = self.query
        self.query = self._MATCH_ALL
        return q

    def _visit_left_right_spec(self, spec: BaseLeftRightSpecification) -> tuple[str, str]:
        spec.left.accept(self)
        left = self._pop_query()
        spec.right.accept(self)
        return left, self._pop_query()

    def visit_and(self, spec: AndSpecification) -> None:
        left, right = self._visit_left_right_spec(spec)
        self.query = f"({left}) ({right})"

    def visit_or(self, spec: OrSpecification) -> None:
        left, right = self._visit_left_right_spec(spec)
        self.query = f"({left} | {right})"

    def visit_not(self, spec: NotSpecification) -> None:
        spec.spec.accept(self)
        self.query = f"-({self._pop_query()})"

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
