from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, func, literal_column, not_, or_, select, true
from sqlalchemy.orm import InstrumentedAttribute

from hike.entity import Entity, TerminalFieldProxy
from hike.persistence.providers.sqlalchemy.dialect import SupportedDialects
from hike.persistence.repository import UnsupportedDialectError
from hike.specifications import ISpecificationVisitor
from hike.specifications.specs import (
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
    RegexSpecification,
)

_EMPTY_FILTER: ColumnElement[Any] = true()


# ---------------------------------------------------------------------------
# Pattern transformers
# ---------------------------------------------------------------------------
# Two rewrites are applied before a pattern reaches certain SQL backends.
#
# 1. _patch_dot — PostgreSQL matches '.' against '\n' by default; every other
#    backend excludes '\n'.  Replace '.' → '[^\n]' for PostgreSQL.
#    Note: (?p) cannot be used instead — it also excludes '\n' from negated
#    bracket expressions like [^x], but RE2 only restricts bare '.'.
#
# 2. _rewrite_posix_for_ascii — MySQL (ICU), MariaDB (PCRE), Oracle, and
#    PostgreSQL (UTF-8 locale) all treat [[:alpha:]], [[:upper:]], [[:lower:]],
#    and [[:alnum:]] as Unicode-aware.  RE2 (the reference engine) treats them
#    as ASCII-only.  Replace the four Unicode-differing classes with explicit
#    ASCII ranges so all backends agree with the in-memory RE2 result.

_POSIX_ASCII: dict[str, str] = {
    "alpha": "A-Za-z",
    "upper": "A-Z",
    "lower": "a-z",
    "alnum": "A-Za-z0-9",
}


def _end_of_bracket(pattern: str, start: int) -> int:
    """Return the index just past the ']' closing the bracket expression at pattern[start]."""
    n = len(pattern)
    i = start + 1  # skip opening [
    if i < n and pattern[i] == "^":
        i += 1
    if i < n and pattern[i] == "]":
        i += 1  # leading ] is literal in POSIX ERE
    while i < n and pattern[i] != "]":
        if pattern[i] != "[" or i + 1 >= n or pattern[i + 1] not in (":", ".", "="):
            i += 1
            continue
        end_ch = pattern[i + 1]
        close = pattern.find(f"{end_ch}]", i + 2)
        i = close + 2 if close >= 0 else n
    return i + 1 if i < n else i


def _patch_dot(pattern: str) -> str:
    """Replace the metachar ``'.'`` with ``'[^\\n]'`` in a POSIX ERE pattern.

    Only characters *outside* bracket expressions are replaced.  The function
    correctly handles POSIX class tokens ``[:..:], [..], [=..=]`` inside
    bracket expressions and skips them verbatim.

    This keeps PostgreSQL's dot behaviour consistent with RE2 and every other
    SQL backend (MySQL, Oracle, MariaDB, SQLite, MSSQL), all of which do not
    match ``'.'`` against ``'\\n'`` by default.  The PostgreSQL embedded flag
    ``(?p)`` cannot be used instead because it also excludes ``'\\n'`` from
    negated bracket expressions (e.g. ``[^x]``), which RE2 still allows to
    match newlines.
    """
    result: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern[i] == ".":
            result.append("[^\n]")
            i += 1
            continue
        if pattern[i] == "[":
            end = _end_of_bracket(pattern, i)
            result.append(pattern[i:end])
            i = end
            continue
        result.append(pattern[i])
        i += 1
    return "".join(result)


def _rewrite_bracket(pattern: str, start: int, result: list[str]) -> int:
    """Process one bracket expression from pattern[start], rewriting POSIX classes; return new index."""
    n = len(pattern)
    i = start + 1  # skip opening [
    result.append("[")
    if i < n and pattern[i] == "^":
        result.append("^")
        i += 1
    if i < n and pattern[i] == "]":
        result.append("]")
        i += 1  # leading ] is literal in POSIX ERE
    while i < n and pattern[i] != "]":
        if pattern[i] != "[" or i + 1 >= n or pattern[i + 1] != ":":
            result.append(pattern[i])
            i += 1
            continue
        close = pattern.find(":]", i + 2)
        name = pattern[i + 2 : close] if close >= 0 else pattern[i + 2 :]
        i = close + 2 if close >= 0 else n
        result.append(_POSIX_ASCII.get(name) or f"[:{name}:]")
    if i < n:
        result.append("]")
        i += 1
    return i


def _rewrite_posix_for_ascii(pattern: str) -> str:
    """Replace Unicode-differing POSIX classes with explicit ASCII ranges.

    Only ``[:alpha:]``, ``[:upper:]``, ``[:lower:]``, and ``[:alnum:]`` are
    rewritten — the four classes whose Unicode vs. ASCII semantics differ.
    All other content (including other POSIX classes) is returned verbatim.
    """
    result: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern[i] != "[":
            result.append(pattern[i])
            i += 1
            continue
        i = _rewrite_bracket(pattern, i, result)
    return "".join(result)



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

    - **Intermediate steps** whose ``field_type`` is an :class:`~hike.entity.Entity`
      subclass are treated as foreign-key relationships.  The mapper is asked for the
      related model class and a JOIN is accumulated.
    - **The leaf step** whose ``field_type`` is a ValueObject is resolved to a
      SQLAlchemy column via the mapper.

    Example::

        mapper = MyMapper()
        visitor = SQLAlchemyEvaluationSpecificationVisitor(Boat, mapper, SupportedDialects.POSTGRESQL, ())
        (Boat.engine.price > 1_000).accept(visitor)
        stmt = visitor.result()
        # → SELECT boat.* FROM boat JOIN engine ON ... WHERE engine.price > 1000

    Regex dialect notes
    -------------------
    POSIX character classes (``[[:alpha:]]``, ``[[:digit:]]``, etc.) are sent
    to the database verbatim.  Unicode-awareness varies by backend:

    * **PostgreSQL** — POSIX ERE via ``~`` / ``~*``; dot-rewrite applied so
      ``'.'`` does not match ``'\\n'``.  Unicode-aware.
    * **MySQL 8** — ``REGEXP_LIKE(col, pat, flag)``; uses ICU (Unicode-aware POSIX classes).
    * **MariaDB** — ``col REGEXP pat``; uses PCRE with ``(?i)`` / ``(?-i)`` inline
      mode modifiers (Unicode-aware in a UTF-8 locale).  ``REGEXP_LIKE`` does not
      exist in MariaDB.
    * **Oracle** — ``REGEXP_LIKE(col, pat, flag)``; Unicode-aware.
    * **SQLite** — user-defined ``REGEXP`` / ``IREGEXP`` UDFs backed by the
      ``regex`` Python module; registered at connection time.  Unicode-aware.
    * **SQL Server 2025 (v17+)** — ``REGEXP_LIKE(col, pat, flag)`` via the RE2
      engine.  **POSIX character classes are ASCII-only in RE2**: ``[[:alpha:]]``
      matches only ``[A-Za-z]``, ``[[:upper:]]`` matches only ``[A-Z]``, etc.
      Use ``\\p{L}`` / ``\\p{Lu}`` / ``\\p{Ll}`` RE2 Unicode escapes when
      Unicode letter matching is required.
    * **Any other dialect, or SQL Server < 2025** — raises
      :class:`~hike.persistence.repository.UnsupportedDialectError`.
    """

    def __init__(
        self,
        aggregate_class: type[Entity[Any]],
        mapper: ISQLAlchemyMapper,
        dialect: SupportedDialects,
        dialect_server_version: tuple[int, ...],
    ) -> None:
        self._mapper = mapper
        self._root_model = mapper.get_model(aggregate_class)
        self._dialect: SupportedDialects = dialect
        # Server version tuple, e.g. (17, 0, 1) for SQL Server 2025.  Used to
        # gate REGEXP_LIKE support (SQL Server 2025+ only, major version >= 17).
        self._dialect_server_version = dialect_server_version
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

    def visit_regex(self, spec: RegexSpecification) -> None:
        col = self.resolve_column(spec.field)
        d = self._dialect

        if d == SupportedDialects.POSTGRESQL:
            # PostgreSQL POSIX ERE: '~' = case-sensitive, '~*' = case-insensitive.
            # Two rewrites are applied:
            # 1. _rewrite_posix_for_ascii: replace Unicode-differing POSIX classes
            #    ([[:alpha:]] etc.) with ASCII ranges to match RE2 reference behaviour.
            # 2. _patch_dot: replace '.' with '[^\n]' because PostgreSQL's '.'
            #    matches newlines by default; all other backends exclude '\n'.
            op = "~*" if spec.case_insensitive else "~"
            self.filters = col.op(op)(_patch_dot(_rewrite_posix_for_ascii(spec.pattern)))

        elif d == SupportedDialects.MYSQL:
            # REGEXP_LIKE(string, pattern, flags):
            #   'c' = case-sensitive (default), 'i' = case-insensitive.
            # MySQL 8 uses ICU which treats [[:alpha:]] etc. as Unicode-aware.
            # Rewrite to ASCII ranges to match RE2 reference behaviour.
            flags = "i" if spec.case_insensitive else "c"
            self.filters = func.REGEXP_LIKE(col, _rewrite_posix_for_ascii(spec.pattern), flags)

        elif d == SupportedDialects.MARIADB:
            # MariaDB has no REGEXP_LIKE; uses the PCRE-based REGEXP operator.
            # Case control uses PCRE inline mode modifiers (collation is typically
            # case-insensitive in utf8mb4_unicode_ci setups).
            # (?i)  — enable case-insensitive matching (overrides collation)
            # (?-i) — force case-sensitive matching regardless of collation
            # MariaDB's PCRE treats [[:alpha:]] etc. as Unicode-aware; rewrite
            # to ASCII ranges to match RE2 reference behaviour.
            prefix = "(?i)" if spec.case_insensitive else "(?-i)"
            self.filters = col.op("REGEXP")(prefix + _rewrite_posix_for_ascii(spec.pattern))

        elif d == SupportedDialects.ORACLE:
            # REGEXP_LIKE(string, pattern, match_parameter):
            #   'c' = case-sensitive, 'i' = case-insensitive.
            # Oracle's regex engine is Unicode-aware for POSIX character classes;
            # rewrite to ASCII ranges to match RE2 reference behaviour.
            flags = "i" if spec.case_insensitive else "c"
            self.filters = func.REGEXP_LIKE(col, _rewrite_posix_for_ascii(spec.pattern), flags)

        elif d == SupportedDialects.SQLITE:
            # SQLite has no built-in regex.  The test fixtures (and any
            # SQLite-backed repository setup) must register REGEXP and IREGEXP
            # as Python UDFs via a SQLAlchemy connect event.  Both use the
            # ``regex`` module so POSIX classes behave identically to in-memory.
            #
            # REGEXP is recognised as a special infix operator by SQLite itself.
            # IREGEXP is a plain UDF called as a function (pattern first, then value).
            if spec.case_insensitive:
                self.filters = func.IREGEXP(spec.pattern, col)
            else:
                self.filters = col.op("REGEXP")(spec.pattern)

        elif d == SupportedDialects.MSSQL and self._dialect_server_version[:1] >= (17,):
            # SQL Server 2025 (major version 17) introduces native REGEXP_LIKE at
            # database compatibility level 170, backed by the RE2 engine.
            # Flags: 'c' = case-sensitive, 'i' = case-insensitive.
            #
            # RE2 POSIX character classes are ASCII-only: [[:alpha:]] = [A-Za-z],
            # [[:upper:]] = [A-Z], [[:lower:]] = [a-z], [[:alnum:]] = [0-9A-Za-z].
            # Use \p{L} / \p{Lu} / \p{Ll} RE2 Unicode escapes for letter matching.
            #
            # literal_column embeds the flag as a SQL literal rather than a bound
            # parameter — SQLAlchemy binds Python strings as nvarchar on MSSQL,
            # but REGEXP_LIKE requires varchar for the flags argument.
            flag_lit: ColumnElement[Any] = literal_column("'i'") if spec.case_insensitive else literal_column("'c'")
            self.filters = func.REGEXP_LIKE(col, spec.pattern, flag_lit)

        else:
            # Only MSSQL < 17 reaches here — unknown dialects are rejected by
            # SupportedDialects.parse() in the repository before the visitor is constructed.
            raise UnsupportedDialectError(
                f"SQL Server {self._dialect_server_version} does not support REGEXP_LIKE. "
                f"Upgrade to SQL Server 2025 (major version 17+)."
            )
