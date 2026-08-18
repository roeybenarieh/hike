# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync              # install dependencies
uv run python <file> # run a script
uv build             # build the package
uv run pyright       # type-check (if pyright is added as a dev dependency)
```

**Always run `uv run pyright <file>` to verify a type-fix actually resolves the warning before declaring it done.**

**Avoid `# type: ignore[...]` comments.** This codebase targets a fully type-hinted library. Reach for a real fix first: overloads, `TYPE_CHECKING` guards, descriptor protocol, or a better type annotation. Only use `# type: ignore` as a last resort when the limitation is in the type checker itself (e.g., Python's type system genuinely cannot express the invariant), and always include the specific error code so the suppression is narrow.

Requires Python >= 3.14. Package manager is `uv` (see `uv.lock`).

## Testing

**Every time you find a bug and fix it, you must write a test that makes sure this bug doesn't exist anymore.**

## Git

Never add a `Co-Authored-By` trailer to commit messages.

Never commit `CLAUDE.md` or anything under `.claude/` — these are local-only.

Use **GitFlow** branching strategy:
- `main` — production-ready releases only
- `develop` — integration branch for features
- `feature/<name>` — branched from and merged back into `develop`
- `release/<version>` — branched from `develop`, merged into both `main` and `develop`
- `hotfix/<name>` — branched from `main`, merged into both `main` and `develop`

## What this is

`hike` is an early-stage Python library of DDD (Domain-Driven Design) building blocks. The intended scope (from README) includes: DDD primitives, generic repository + Unit of Work, event-driven patterns, hexagonal architecture, and CQRS — most of which are not yet implemented.

## Architecture: `src/hike/ddd/`

### Type hierarchy

```
DomainObject                         # marker base (common.py); DomainError extends Exception
├── ValueObject                      # frozen dataclass; equality by .value (value_object.py)
│   └── EntityID[TId: Hashable]      # frozen dataclass wrapping the raw ID (entity.py)
│       └── AggregateID[TId: Hashable]  (aggregate.py)
│           └── AggregateUUID        # AggregateID[UUID] convenience alias
├── Entity[TId: Hashable]            # has .id: EntityID[TId]; eq/hash by id (entity.py)
│   └── Aggregate                    # generic via old-style TId TypeVar; adds domain events (aggregate.py)
├── DomainEvent                      # marker (domain_event.py)
└── Rule[obj: DomainObject]          # abstract business rule; call raise_on_broken_rule() (aggregate.py)
```

### Repository pattern

`IRepository(Generic[TId, TAggregate])` in `repository.py` — two independent TypeVars:
- `TId = TypeVar("TId", bound=AggregateID)`
- `TAggregate = TypeVar("TAggregate", bound=Aggregate)`

Python's type system cannot statically enforce that `TAggregate`'s ID type equals `TId` (no higher-kinded types). This is noted with a `# HACK` comment on the class. Subclasses must keep them consistent by convention.

Repository error hierarchy (all in `repository.py`, extend `DomainError`):
- `RepositoryError` → `DBConnectionError`, `UnknownError`, `AggregateError`
- `AggregateError` → `AggregateDoesNotExistError`, `AggregateAlreadyExistError`

`DBContext` and `UnitOfWork[TRepos: NamedTuple]` live in `uow.py`. `UnitOfWork` takes a NamedTuple of `IRepository` instances and wraps a `DBContext` for transaction management (`begin`/`commit`/`rollback`/`close`).

### Provider parity rule

Every feature **must work identically across all providers** (in_memory, pymongo, sqlalchemy, redis). If a feature cannot be implemented in even one provider, it must not be available in any provider — remove or withhold it entirely rather than offering a partial implementation. Do not add a capability to three providers and raise `NotImplementedError` in the fourth.

### Specification pattern (`specifications/`)

`ISpecification` (in `specifications/interfaces.py`) supports composition via `&`, `|`, `~` operators, which produce `AndSpecification`, `OrSpecification`, `NotSpecification`. Leaf specs (`EqualSpecification`, `GreaterThanSpecification`, etc.) extend `BaseFilterSpecification` with `(value, operand)`.

Implementations traverse specs via the **Visitor pattern**: `IVisitor` (also in `interfaces.py`) declares `visit_*` methods for each spec type. Subclass `IVisitor` to translate a spec tree into a query (e.g. SQL WHERE clause).

`ComparableObject` (in `specifications/comperable.py`) uses a metaclass so that class-level comparisons (`MyField == value`) return specification objects instead of booleans. Intended for ergonomic query building.

> **Note**: `ddd/specification_visitor.py` at the root appears to be a stale duplicate — it imports from `.specification` which does not exist. The canonical visitor interface is `specifications/interfaces.py`.

### Typing style

The codebase mixes **PEP 695 new-style type parameters** (e.g. `class Entity[TId: Hashable]`) with **old-style `TypeVar`** (e.g. `TId = TypeVar("TId", bound=AggregateID)` for `Aggregate`). The mix exists because Pyright rejects `TAggregate: Aggregate[TId]` as a bound (generic alias parameterized by a TypeVar), so `Aggregate` uses old-style TypeVar to allow `IRepository(Generic[TId, TAggregate])` to work without Pyright errors.