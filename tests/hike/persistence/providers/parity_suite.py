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

import threading
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4


import re2
import pytest

from hike.persistence.ordering import asc, desc
from hike.persistence.pagination import CursorPagination, OffsetPagination, Page, PagePagination
from hike.persistence.repository import (
    LockConflictError,
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
            fetched = repo.get_one(boat.get_id())
        assert fetched.name == Name("Sea Spirit")
        assert fetched.price == Price(4_999.99)

    def test_save_many_and_get_each(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        boats = [Boat(name=Name(n), price=Price(p)) for n, p in [("X", 1.0), ("Y", 2.0), ("Z", 3.0)]]
        with uow(repo):
            ids = repo.save_many(boats)
            uow.commit()
        assert len(ids) == 3
        with uow(repo):
            for boat in boats:
                fetched = repo.get_one(boat.get_id())
                assert fetched.name == boat.name

    def test_save_many_raises_on_duplicate(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        boat = Boat(name=Name("Dup"), price=Price(1.0))
        with uow(repo):
            repo.save(boat)
            uow.commit()
        with pytest.raises(ResourceAlreadyExistError):
            with uow(repo):
                repo.save_many([Boat(name=Name("New"), price=Price(2.0)), boat])
                uow.commit()

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
            fetched = repo.get_one(boat.get_id())
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

    # ------------------------------------------------------------------
    # Regex specification — POSIX ERE parity across all providers
    # ------------------------------------------------------------------
    # Each test saves its own isolated data and verifies that the regex
    # specification produces the same results regardless of provider.
    #
    # POSIX ERE constructs covered:
    #   Named character classes · Custom ranges · Anchors · Quantifiers
    #   Dot · Alternation · Grouping · Case insensitivity · Composition
    # ------------------------------------------------------------------

    # Named character classes

    def test_regex_class_alpha(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("Letters"), price=Price(10.0)))   # all alpha
            repo.save(Boat(name=Name("Let3rs"), price=Price(20.0)))    # alpha + digit
            repo.save(Boat(name=Name("123"), price=Price(30.0)))       # no alpha
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^[[:alpha:]]+$"))
        assert {r.name for r in results} == {Name("Letters")}

    def test_regex_class_digit(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("Abc"), price=Price(10.0)))       # no digit
            repo.save(Boat(name=Name("Abc1"), price=Price(20.0)))      # has digit
            repo.save(Boat(name=Name("999"), price=Price(30.0)))       # all digits
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"[[:digit:]]"))
        assert {r.name for r in results} == {Name("Abc1"), Name("999")}

    def test_regex_class_alnum(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("Hello123"), price=Price(10.0)))  # alnum only
            repo.save(Boat(name=Name("Hello-123"), price=Price(20.0))) # has hyphen
            repo.save(Boat(name=Name("Hi There"), price=Price(30.0)))  # has space
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^[[:alnum:]]+$"))
        assert {r.name for r in results} == {Name("Hello123")}

    def test_regex_class_upper(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("hello"), price=Price(10.0)))     # no uppercase
            repo.save(Boat(name=Name("Hello"), price=Price(20.0)))     # has uppercase
            repo.save(Boat(name=Name("HELLO"), price=Price(30.0)))     # all uppercase
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"[[:upper:]]"))
        assert {r.name for r in results} == {Name("Hello"), Name("HELLO")}

    def test_regex_class_lower(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("HELLO"), price=Price(10.0)))     # no lowercase
            repo.save(Boat(name=Name("Hello"), price=Price(20.0)))     # has lowercase
            repo.save(Boat(name=Name("hello"), price=Price(30.0)))     # all lowercase
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"[[:lower:]]"))
        assert {r.name for r in results} == {Name("Hello"), Name("hello")}

    def test_regex_class_space(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("NoSpace"), price=Price(10.0)))
            repo.save(Boat(name=Name("Has Space"), price=Price(20.0)))
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"[[:space:]]"))
        assert {r.name for r in results} == {Name("Has Space")}

    def test_regex_class_punct(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("NoPunct"), price=Price(10.0)))
            repo.save(Boat(name=Name("Has-Dash"), price=Price(20.0)))
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"[[:punct:]]"))
        assert {r.name for r in results} == {Name("Has-Dash")}

    def test_regex_negated_class(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("abc"), price=Price(10.0)))       # no digits
            repo.save(Boat(name=Name("a1b"), price=Price(20.0)))       # has digit
            repo.save(Boat(name=Name("123"), price=Price(30.0)))       # all digits
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^[^[:digit:]]+$"))
        assert {r.name for r in results} == {Name("abc")}

    # Custom character ranges

    def test_regex_range_az(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("alpha"), price=Price(10.0)))     # all a-z
            repo.save(Boat(name=Name("Alpha"), price=Price(20.0)))     # has uppercase
            repo.save(Boat(name=Name("123"), price=Price(30.0)))       # digits only
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^[a-z]+$"))
        assert {r.name for r in results} == {Name("alpha")}

    def test_regex_range_AZ(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("ALPHA"), price=Price(10.0)))     # all A-Z
            repo.save(Boat(name=Name("Alpha"), price=Price(20.0)))     # mixed case
            repo.save(Boat(name=Name("alpha"), price=Price(30.0)))     # all lowercase
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^[A-Z]+$"))
        assert {r.name for r in results} == {Name("ALPHA")}

    def test_regex_range_09(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("abc"), price=Price(10.0)))       # no digits
            repo.save(Boat(name=Name("a1b"), price=Price(20.0)))       # has digit
            repo.save(Boat(name=Name("007"), price=Price(30.0)))       # all digits
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"[0-9]"))
        assert {r.name for r in results} == {Name("a1b"), Name("007")}

    # Anchors

    def test_regex_anchor_start(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("SeaSpirit"), price=Price(10.0)))
            repo.save(Boat(name=Name("BlueSea"), price=Price(20.0)))
            repo.save(Boat(name=Name("Sea"), price=Price(30.0)))
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^Sea"))
        assert {r.name for r in results} == {Name("SeaSpirit"), Name("Sea")}

    def test_regex_anchor_end(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("SeaSpirit"), price=Price(10.0)))
            repo.save(Boat(name=Name("SpiritBlue"), price=Price(20.0)))
            repo.save(Boat(name=Name("Spirit"), price=Price(30.0)))
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"Spirit$"))
        assert {r.name for r in results} == {Name("SeaSpirit"), Name("Spirit")}

    def test_regex_anchor_exact_match(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("Alpha"), price=Price(10.0)))
            repo.save(Boat(name=Name("AlphaBoat"), price=Price(20.0)))
            repo.save(Boat(name=Name("BetaAlpha"), price=Price(30.0)))
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^Alpha$"))
        assert {r.name for r in results} == {Name("Alpha")}

    # Quantifiers

    def test_regex_quantifier_one_or_more(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("123"), price=Price(10.0)))   # all digits
            repo.save(Boat(name=Name("12A"), price=Price(20.0)))   # ends with letter
            repo.save(Boat(name=Name("Abc"), price=Price(30.0)))   # no digits
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^[[:digit:]]+$"))
        assert {r.name for r in results} == {Name("123")}

    def test_regex_quantifier_zero_or_one(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("A"), price=Price(10.0)))     # matches A + zero s
            repo.save(Boat(name=Name("As"), price=Price(20.0)))    # matches A + one s
            repo.save(Boat(name=Name("Ass"), price=Price(30.0)))   # too many s
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^As?$"))
        assert {r.name for r in results} == {Name("A"), Name("As")}

    def test_regex_quantifier_exact_count(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("Al"), price=Price(10.0)))        # 2 chars
            repo.save(Boat(name=Name("Alpha"), price=Price(20.0)))     # 5 chars
            repo.save(Boat(name=Name("AlphaBeta"), price=Price(30.0))) # 9 chars
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^[[:alpha:]]{5}$"))
        assert {r.name for r in results} == {Name("Alpha")}

    def test_regex_quantifier_min_count(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("AB"), price=Price(10.0)))    # 2, too few
            repo.save(Boat(name=Name("ABC"), price=Price(20.0)))   # 3, at min
            repo.save(Boat(name=Name("ABCDE"), price=Price(30.0))) # 5, over min
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^[[:alpha:]]{3,}$"))
        assert {r.name for r in results} == {Name("ABC"), Name("ABCDE")}

    def test_regex_quantifier_range_count(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("AB"), price=Price(10.0)))     # 2 — too few
            repo.save(Boat(name=Name("ABC"), price=Price(20.0)))    # 3 — in range
            repo.save(Boat(name=Name("ABCDE"), price=Price(30.0)))  # 5 — in range
            repo.save(Boat(name=Name("ABCDEF"), price=Price(40.0))) # 6 — too many
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^[[:alpha:]]{3,5}$"))
        assert {r.name for r in results} == {Name("ABC"), Name("ABCDE")}

    # Dot (any character)

    def test_regex_dot(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("Alpha"), price=Price(10.0)))  # A-l-pha → matches A.pha
            repo.save(Boat(name=Name("Axpha"), price=Price(20.0)))  # A-x-pha → matches A.pha
            repo.save(Boat(name=Name("Apha"), price=Price(30.0)))   # too short for A.pha
            repo.save(Boat(name=Name("Boat"), price=Price(40.0)))   # no match
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"A.pha"))
        assert {r.name for r in results} == {Name("Alpha"), Name("Axpha")}

    # Alternation

    def test_regex_alternation(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("Alpha"), price=Price(10.0)))
            repo.save(Boat(name=Name("Beta"), price=Price(20.0)))
            repo.save(Boat(name=Name("Gamma"), price=Price(30.0)))
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"Alpha|Beta"))
        assert {r.name for r in results} == {Name("Alpha"), Name("Beta")}

    # Grouping

    def test_regex_grouping(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("Alpha"), price=Price(10.0)))
            repo.save(Boat(name=Name("Alice"), price=Price(20.0)))
            repo.save(Boat(name=Name("Beta"), price=Price(30.0)))
            repo.save(Boat(name=Name("Gamma"), price=Price(40.0)))
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^(Al|Be)"))
        assert {r.name for r in results} == {Name("Alpha"), Name("Alice"), Name("Beta")}

    # Case insensitivity

    def test_regex_case_insensitive(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("Alpha"), price=Price(10.0)))
            repo.save(Boat(name=Name("ALPHA"), price=Price(20.0)))
            repo.save(Boat(name=Name("beta"), price=Price(30.0)))
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"alpha", case_insensitive=True))
        assert {r.name for r in results} == {Name("Alpha"), Name("ALPHA")}

    def test_regex_case_sensitive_default(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("Alpha"), price=Price(10.0)))  # starts with uppercase A
            repo.save(Boat(name=Name("alpha"), price=Price(20.0)))  # starts with lowercase a
            repo.save(Boat(name=Name("ALPHA"), price=Price(30.0)))  # starts with uppercase A
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^[A-Z]"))
        assert {r.name for r in results} == {Name("Alpha"), Name("ALPHA")}

    def test_get_many_with_regex_spec(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("Alpha"), price=Price(10.0)))
            repo.save(Boat(name=Name("Beta"), price=Price(20.0)))
            repo.save(Boat(name=Name("Gamma"), price=Price(30.0)))
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^[AB]"))
        assert len(results) == 2
        assert {r.name for r in results} == {Name("Alpha"), Name("Beta")}

    def test_get_many_with_regex_spec_case_insensitive(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("Alpha"), price=Price(10.0)))
            repo.save(Boat(name=Name("BETA"), price=Price(20.0)))
            repo.save(Boat(name=Name("gamma"), price=Price(30.0)))
            uow.commit()
        with uow(repo):
            # [A-Z] is used instead of [[:upper:]] because POSIX character
            # classes are not expanded by case folding in all SQL engines
            # (Oracle and MSSQL do not match lowercase 'g' against [[:upper:]]
            # even with the i flag).  [A-Z] with i is universally expanded to
            # [A-Za-z] across all providers.
            results = repo.get_many(Boat.name.matches(r"^[A-Z]", case_insensitive=True))
        assert len(results) == 3

    # ------------------------------------------------------------------
    # RE2 validation — construction-time rejection of unsupported constructs
    # ------------------------------------------------------------------

    def test_regex_rejects_lookahead(self) -> None:
        """Lookaheads are not supported by RE2."""
        with pytest.raises(re2.error):  # type: ignore[attr-defined]
            Boat.name.matches(r"(?=foo)")

    def test_regex_rejects_backreference(self) -> None:
        """Backreferences are not supported by RE2."""
        with pytest.raises(re2.error):  # type: ignore[attr-defined]
            Boat.name.matches(r"(ab)\1")


    # dot behaviour

    def test_regex_flag_dot_nl_false_dot_does_not_match_newline(
        self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]
    ) -> None:
        """dot_nl=False: '.' must not match '\\n' across all providers."""
        with uow(repo):
            repo.save(Boat(name=Name("axb"), price=Price(10.0)))   # x in middle → matches ^a.b$
            repo.save(Boat(name=Name("a\nb"), price=Price(20.0)))  # newline in middle → no match
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^a.b$"))
        assert {r.name for r in results} == {Name("axb")}

    # literal=False (default)

    def test_regex_flag_literal_false_special_chars_are_operators(
        self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]
    ) -> None:
        """literal=False: '.' is a regex metacharacter matched against any single char, not a literal period."""
        with uow(repo):
            repo.save(Boat(name=Name("AxB"), price=Price(10.0)))  # . matches x
            repo.save(Boat(name=Name("A.B"), price=Price(20.0)))  # . also matches literal period
            repo.save(Boat(name=Name("AB"), price=Price(30.0)))   # no char in middle → no match
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^A.B$"))
        assert {r.name for r in results} == {Name("AxB"), Name("A.B")}

    # encoding=UTF8 (default)

    def test_regex_flag_encoding_utf8_dot_matches_multibyte_char_as_one(
        self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]
    ) -> None:
        """encoding=UTF8: '.' matches a multi-byte Unicode code point as one logical character."""
        with uow(repo):
            repo.save(Boat(name=Name("Héros"), price=Price(10.0)))  # é = U+00E9 (2 UTF-8 bytes)
            repo.save(Boat(name=Name("Heros"), price=Price(20.0)))
            repo.save(Boat(name=Name("naïve"), price=Price(30.0)))  # ï = U+00EF; no H prefix
            uow.commit()
        with uow(repo):
            # ^H.ros$ needs exactly H + [one code point] + ros; matches both H-é-ros and H-e-ros
            results = repo.get_many(Boat.name.matches(r"^H.ros$"))
        assert {r.name for r in results} == {Name("Héros"), Name("Heros")}

    # case_sensitive (exposed via case_insensitive= parameter) — see test_regex_case_insensitive
    # and test_regex_case_sensitive_default above for the full behavioural coverage.

    # one_line=True — ^ and $ are string-boundary anchors, not line-boundary anchors

    def test_regex_anchor_caret_is_string_boundary(
        self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]
    ) -> None:
        """^ must match only at the start of the whole string, not after embedded newlines."""
        with uow(repo):
            repo.save(Boat(name=Name("foo\nbar"), price=Price(10.0)))  # ^ should NOT match "bar" here
            repo.save(Boat(name=Name("bar"), price=Price(20.0)))       # ^ SHOULD match "bar" here
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^bar"))
        assert {r.name for r in results} == {Name("bar")}

    def test_regex_anchor_dollar_is_string_boundary(
        self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]
    ) -> None:
        """$ must match only at the end of the whole string, not before embedded newlines."""
        with uow(repo):
            repo.save(Boat(name=Name("foo\nbar"), price=Price(10.0)))  # $ should NOT match "foo" here
            repo.save(Boat(name=Name("foo"), price=Price(20.0)))       # $ SHOULD match "foo" here
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"foo$"))
        assert {r.name for r in results} == {Name("foo")}

    # [^x] negated class must still match \n (dot_nl=False only affects '.' metachar)

    def test_regex_negated_class_matches_newline(
        self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]
    ) -> None:
        """[^x] must match \\n — negated class is not limited by dot_nl=False."""
        with uow(repo):
            repo.save(Boat(name=Name("a\nb"), price=Price(10.0)))  # \n is not 'x' → matches [^x]
            repo.save(Boat(name=Name("axb"), price=Price(20.0)))   # 'x' is 'x' → no match
            repo.save(Boat(name=Name("ayb"), price=Price(30.0)))   # 'y' is not 'x' → matches
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^a[^x]b$"))
        assert {r.name for r in results} == {Name("a\nb"), Name("ayb")}

    # [[:alpha:]] ASCII-only parity — must not match accented/non-ASCII letters

    def test_regex_class_alpha_is_ascii_only(
        self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]
    ) -> None:
        """[[:alpha:]] matches only ASCII letters A-Za-z — not accented Unicode letters.

        RE2 (the reference engine) treats [[:alpha:]] as ASCII-only.  All supported
        backends are configured to match this behaviour, so accented letters like
        é and ï are NOT matched by [[:alpha:]] in any provider.
        """
        with uow(repo):
            repo.save(Boat(name=Name("cafe"), price=Price(10.0)))     # pure ASCII → matches
            repo.save(Boat(name=Name("café"), price=Price(20.0)))     # é is non-ASCII → no match
            repo.save(Boat(name=Name("naïve"), price=Price(30.0)))    # ï is non-ASCII → no match
            repo.save(Boat(name=Name("café123"), price=Price(40.0)))  # has digits → no match
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^[[:alpha:]]+$"))
        assert {r.name for r in results} == {Name("cafe")}

    # ------------------------------------------------------------------
    # Composition with other specifications

    def test_regex_and_with_range_spec(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("Alpha"), price=Price(10.0)))  # uppercase start, low price
            repo.save(Boat(name=Name("BETA"), price=Price(30.0)))   # uppercase start, high price
            repo.save(Boat(name=Name("gamma"), price=Price(40.0)))  # lowercase start, high price
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^[[:upper:]]") & (Boat.price > 20.0))
        assert {r.name for r in results} == {Name("BETA")}

    def test_regex_or_with_range_spec(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("Alpha"), price=Price(10.0)))  # uppercase start, low price
            repo.save(Boat(name=Name("BETA"), price=Price(30.0)))   # uppercase start, high price
            repo.save(Boat(name=Name("gamma"), price=Price(40.0)))  # lowercase start, high price
            repo.save(Boat(name=Name("delta"), price=Price(5.0)))   # lowercase start, low price
            uow.commit()
        with uow(repo):
            results = repo.get_many(Boat.name.matches(r"^[[:upper:]]") | (Boat.price > 20.0))
        assert {r.name for r in results} == {Name("Alpha"), Name("BETA"), Name("gamma")}

    def test_regex_not_composition(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        with uow(repo):
            repo.save(Boat(name=Name("Alpha"), price=Price(10.0)))  # starts uppercase
            repo.save(Boat(name=Name("alpha"), price=Price(20.0)))  # starts lowercase
            repo.save(Boat(name=Name("BETA"), price=Price(30.0)))   # starts uppercase
            uow.commit()
        with uow(repo):
            results = repo.get_many(~Boat.name.matches(r"^[[:upper:]]"))
        assert {r.name for r in results} == {Name("alpha")}

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
            fetched = repo.get_one(boat.get_id())
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
                repo.get_one(boat.get_id())

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
            fetched = repo.get_one(boat.get_id())
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
                repo.get_one(uuid4())

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
                repo.get_one(boat.get_id())

    def test_optimistic_lock_conflict(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        boat = Boat(name=Name("Contested"), price=Price(100.0))
        with uow(repo):
            repo.save(boat)
            uow.commit()
        with uow(repo):
            copy_a = repo.get_one(boat.get_id())
        with uow(repo):
            copy_b = repo.get_one(boat.get_id())
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
            fetched = journey_repo.get_one(journey.get_id())
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
            fetched = journey_repo.get_one(journey.get_id())
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

    # ------------------------------------------------------------------
    # watch
    # ------------------------------------------------------------------

    @pytest.fixture
    def watch_repo(self, repo: Any) -> Any:
        """Repository used by watch() tests. Default: same as repo.

        Override in provider-specific test classes when watch() needs extra
        setup (e.g. SQLAlchemy requires a session_factory).
        """
        return repo

    def test_watch_yields_new_insert(
        self,
        uow: UnitOfWork[Any],
        repo: IRepository[UUID, Boat, Any],
        watch_repo: IRepository[UUID, Boat, Any],
    ) -> None:
        boat = Boat(name=Name("Watcher Boat"), price=Price(42.0))
        results: list[Boat] = []

        def run_watch() -> None:
            for item in watch_repo.watch():
                results.append(item)  # type: ignore[arg-type]
                break

        thread = threading.Thread(target=run_watch, daemon=True)
        thread.start()

        with uow(repo):
            repo.save(boat)
            uow.commit()

        thread.join(timeout=8.0)
        assert not thread.is_alive(), "watch() did not yield an item within 8 s"
        assert len(results) == 1
        assert results[0].name == Name("Watcher Boat")
        assert results[0].price == Price(42.0)

    def test_watch_include_existing_yields_preexisting(
        self,
        uow: UnitOfWork[Any],
        repo: IRepository[UUID, Boat, Any],
        watch_repo: IRepository[UUID, Boat, Any],
    ) -> None:
        boat_a = Boat(name=Name("Alpha"), price=Price(1.0))
        boat_b = Boat(name=Name("Beta"), price=Price(2.0))
        with uow(repo):
            repo.save(boat_a)
            repo.save(boat_b)
            uow.commit()

        results: list[Boat] = []

        def run_watch() -> None:
            for item in watch_repo.watch(include_existing=True):
                results.append(item)  # type: ignore[arg-type]
                if len(results) == 2:
                    break

        thread = threading.Thread(target=run_watch, daemon=True)
        thread.start()
        thread.join(timeout=8.0)
        assert not thread.is_alive(), "watch(include_existing=True) did not yield existing items within 8 s"
        assert {r.name for r in results} == {Name("Alpha"), Name("Beta")}

    def test_watch_include_existing_no_duplicates(
        self,
        uow: UnitOfWork[Any],
        repo: IRepository[UUID, Boat, Any],
        watch_repo: IRepository[UUID, Boat, Any],
    ) -> None:
        existing = Boat(name=Name("Existing"), price=Price(1.0))
        with uow(repo):
            repo.save(existing)
            uow.commit()

        new_boat = Boat(name=Name("New"), price=Price(2.0))
        results: list[Boat] = []

        def run_watch() -> None:
            for item in watch_repo.watch(include_existing=True):
                results.append(item)  # type: ignore[arg-type]
                if len(results) == 2:
                    break

        thread = threading.Thread(target=run_watch, daemon=True)
        thread.start()

        with uow(repo):
            repo.save(new_boat)
            uow.commit()

        thread.join(timeout=8.0)
        assert not thread.is_alive(), "watch(include_existing=True) did not yield 2 items within 8 s"
        assert len(results) == 2
        assert {r.name for r in results} == {Name("Existing"), Name("New")}

    # ------------------------------------------------------------------
    # is_modified / refresh
    # ------------------------------------------------------------------

    def test_is_modified_false_after_save(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        boat = Boat(name=Name("Fresh"), price=Price(1.0))
        with uow(repo):
            repo.save(boat)
            uow.commit()
        with uow(repo):
            assert repo.is_modified(boat) is False

    def test_is_modified_true_after_external_update(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        boat = Boat(name=Name("Stale"), price=Price(1.0))
        with uow(repo):
            repo.save(boat)
            uow.commit()
        with uow(repo):
            copy = repo.get_one(boat.get_id())
            copy.price = Price(2.0)
            repo.update(copy)
            uow.commit()
        with uow(repo):
            assert repo.is_modified(boat) is True

    def test_is_modified_raises_for_nonexistent(self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]) -> None:
        ghost = Boat(name=Name("Ghost"), price=Price(1.0))
        with uow(repo):
            with pytest.raises(ResourceDoesNotExistError):
                repo.is_modified(ghost)



class CrossProcessWatchParitySuite:
    """Parity suite for the cross-process contract of ``IRepository.watch()``.

    InMemory state is process-local and therefore cannot satisfy this contract,
    so this suite is intentionally separate from ``RepositoryParitySuite``.
    Concrete test classes for SQLAlchemy, PyMongo, and Redis inherit from
    **both** ``RepositoryParitySuite`` and this class.

    Subclasses must provide:

    - ``watch_repo`` — a repository whose ``watch()`` is under test (may be
      the same object as ``repo`` or a separate instance with its own session).
    - ``cross_process_insert`` — a callable ``(Boat) -> None`` that inserts
      the given boat from a freshly spawned OS process.
    """

    def test_watch_cross_process_insert(
        self,
        watch_repo: IRepository[UUID, Boat, Any],
        cross_process_insert: Callable[[Boat], None],
    ) -> None:
        """watch() must yield inserts that originate from a separate OS process."""
        boat = Boat(name=Name("Cross Process Boat"), price=Price(77.0))
        results: list[Boat] = []

        def run_watch() -> None:
            for item in watch_repo.watch():
                results.append(item)  # type: ignore[arg-type]
                break

        thread = threading.Thread(target=run_watch, daemon=True)
        thread.start()
        cross_process_insert(boat)

        thread.join(timeout=10.0)
        assert not thread.is_alive(), "watch() did not yield a cross-process insert within 10 s"
        assert len(results) == 1
        assert results[0].name == Name("Cross Process Boat")
        assert results[0].price == Price(77.0)


class LockParitySuite:
    """Parity suite for :meth:`~hike.persistence.repository.IRepository.acquire_lock`,
    :meth:`~hike.persistence.repository.IRepository.release_lock`, and
    :meth:`~hike.persistence.repository.IRepository.locked`.

    Subclasses must provide the same ``uow`` and ``repo`` fixtures as
    :class:`RepositoryParitySuite`.
    """

    def test_lock_acquire_and_release(
        self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]
    ) -> None:
        """Releasing a lock lets a different owner acquire it."""
        boat_id = uuid4()
        with uow(repo):
            repo.acquire_lock(boat_id, owner="owner-a")
            repo.release_lock(boat_id, owner="owner-a")
            repo.acquire_lock(boat_id, owner="owner-b")
            repo.release_lock(boat_id, owner="owner-b")

    def test_lock_reentrant_same_owner(
        self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]
    ) -> None:
        """The same owner can re-acquire a lock it already holds (no deadlock)."""
        boat_id = uuid4()
        with uow(repo):
            repo.acquire_lock(boat_id, owner="owner-a")
            repo.acquire_lock(boat_id, owner="owner-a")  # reentrant — must not block
            repo.release_lock(boat_id, owner="owner-a")

    def test_locked_context_manager_releases_on_exit(
        self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]
    ) -> None:
        """``locked()`` releases the lock on normal exit."""
        boat_id = uuid4()
        with uow(repo):
            with repo.locked(boat_id, owner="owner-a"):
                pass
            repo.acquire_lock(boat_id, owner="owner-b")
            repo.release_lock(boat_id, owner="owner-b")

    def test_locked_context_manager_releases_on_exception(
        self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]
    ) -> None:
        """``locked()`` releases the lock even when the body raises."""
        boat_id = uuid4()
        with uow(repo):
            with pytest.raises(RuntimeError):
                with repo.locked(boat_id, owner="owner-a"):
                    raise RuntimeError("boom")
            repo.acquire_lock(boat_id, owner="owner-b")
            repo.release_lock(boat_id, owner="owner-b")

    def test_lock_conflict_raises_after_timeout(
        self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]
    ) -> None:
        """``acquire_lock`` raises ``LockConflictError`` when another owner holds the lock."""
        boat_id = uuid4()
        with uow(repo):
            repo.acquire_lock(boat_id, owner="owner-a")
            with pytest.raises(LockConflictError):
                repo.acquire_lock(boat_id, owner="owner-b", timeout=0.05)
            repo.release_lock(boat_id, owner="owner-a")

    def test_release_wrong_owner_is_noop(
        self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]
    ) -> None:
        """``release_lock`` with the wrong owner silently no-ops."""
        boat_id = uuid4()
        with uow(repo):
            repo.acquire_lock(boat_id, owner="owner-a")
            repo.release_lock(boat_id, owner="owner-b")  # wrong owner — noop
            with pytest.raises(LockConflictError):
                repo.acquire_lock(boat_id, owner="owner-c", timeout=0.05)
            repo.release_lock(boat_id, owner="owner-a")

    def test_lock_persists_across_uow_sessions(
        self, uow: UnitOfWork[Any], repo: IRepository[UUID, Boat, Any]
    ) -> None:
        """A lock acquired in one UoW session persists into the next session."""
        boat_id = uuid4()
        with uow(repo):
            repo.acquire_lock(boat_id, owner="saga-a")
        with uow(repo):
            with pytest.raises(LockConflictError):
                repo.acquire_lock(boat_id, owner="saga-b", timeout=0.05)
        with uow(repo):
            repo.release_lock(boat_id, owner="saga-a")
        with uow(repo):
            repo.acquire_lock(boat_id, owner="saga-b")
            repo.release_lock(boat_id, owner="saga-b")

    @pytest.fixture
    def short_ttl_repo(self) -> Any:
        """Repository configured with a very short lock_ttl (e.g. 0.2 s) for expiry tests.

        Override in provider-specific subclasses.  The base implementation skips
        the test so providers that have not wired this fixture yet are not silently
        broken.
        """
        pytest.skip("short_ttl_repo not configured for this provider")

    def test_lock_expires_after_ttl(
        self, uow: UnitOfWork[Any], short_ttl_repo: IRepository[UUID, Boat, Any]
    ) -> None:
        """A lock auto-expires (via constructor lock_ttl), letting another owner acquire it."""
        boat_id = uuid4()
        with uow(short_ttl_repo):
            short_ttl_repo.acquire_lock(boat_id, owner="owner-a")
        # Don't release — simulate a crash. After the TTL the lock must be gone.
        time.sleep(0.35)
        with uow(short_ttl_repo):
            short_ttl_repo.acquire_lock(boat_id, owner="owner-b")  # must succeed
            short_ttl_repo.release_lock(boat_id, owner="owner-b")

    def test_lock_ttl_reentrant_refreshes_expiry(
        self, uow: UnitOfWork[Any], short_ttl_repo: IRepository[UUID, Boat, Any]
    ) -> None:
        """Re-acquiring with the same owner refreshes the TTL dead-man's switch.

        Timeline (TTL = 0.2 s):
          t=0.00  acquire → expires t=0.20
          t=0.10  reentrant acquire → refreshed expiry = t=0.30
          t=0.22  check: past original expiry (0.20), before refreshed expiry (0.30)
        """
        boat_id = uuid4()
        with uow(short_ttl_repo):
            short_ttl_repo.acquire_lock(boat_id, owner="owner-a")
        time.sleep(0.10)  # t=0.10 — still within TTL
        with uow(short_ttl_repo):
            short_ttl_repo.acquire_lock(boat_id, owner="owner-a")  # reentrant → refreshed to t=0.30
        time.sleep(0.12)  # t=0.22 — past original expiry (0.20), 0.08 s before refreshed (0.30)
        with uow(short_ttl_repo):
            with pytest.raises(LockConflictError):
                short_ttl_repo.acquire_lock(boat_id, owner="owner-b", timeout=0.05)
            short_ttl_repo.release_lock(boat_id, owner="owner-a")
