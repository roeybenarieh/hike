# SQLAlchemy Mappers

Hike ships four SQLAlchemy mapper classes that bridge your domain entities to ORM models. Choose the one that fits your schema:

| Mapper | When to use |
| :--- | :--- |
| `DictAutoSQLAlchemyMapper` | Zero ORM boilerplate — Hike infers the full relational schema from your entity annotations |
| `FlatAutoSQLAlchemyMapper` | Zero ORM boilerplate — like `DictAutoSQLAlchemyMapper` but uses one flat table instead of joins |
| `DictSQLAlchemyMapper` | You control the ORM models; one table per entity class |
| `FlatSQLAlchemyMapper` | You control the ORM model; everything on one table with prefixed column names |

All four handle **optimistic locking** automatically — concurrent writes to the same aggregate are detected and rejected safely, with no extra schema work on your part.

---

## 1. `DictAutoSQLAlchemyMapper` — Zero Boilerplate (default)

The easiest path — and the default. When you don't pass a mapper to `SQLAlchemyRepository`, Hike creates a `DictAutoSQLAlchemyMapper` for you automatically:

```python
from hike.ddd.providers.sqlalchemy import SQLAlchemyRepository

repo = SQLAlchemyRepository(MotorBoat)    # mapper inferred automatically
```

Hike generates ORM models, table names, columns, foreign keys, and relationships from the aggregate's field annotations. If you need the tables to share a `DeclarativeBase` with the rest of your app (e.g. for `Base.metadata.create_all()`), pass it explicitly:

```python
from sqlalchemy.orm import DeclarativeBase
from hike.ddd.providers.sqlalchemy import DictAutoSQLAlchemyMapper, SQLAlchemyRepository

class Base(DeclarativeBase): ...

mapper = DictAutoSQLAlchemyMapper(MotorBoat, base=Base)
Base.metadata.create_all(engine)          # creates motor_boats and boat_engines tables
repo = SQLAlchemyRepository(MotorBoat, mapper)
```

Table names follow the pattern `PascalCase` → `snake_case_plural` (e.g. `MotorBoat` → `motor_boats`).

### What gets inferred

- `Field[ValueObject]` → scalar column (`String`, `Float`, `Integer`, `Uuid`, `Boolean`, etc.)
- `Optional[Field[ValueObject]]` → `nullable=True` column
- `Field[Entity]` → FK column + `relationship()` (one row per child entity)
- `list[Entity]` → cascade `relationship()` with `"all, delete-orphan"`
- Aggregate roots get an internal column for safe concurrent writes (managed entirely by Hike)

### Limitations

- Column length/precision cannot be inferred; uses unbounded `String`, `Float`, etc.
- No index or unique constraint inference
- Column names always match field names; overrides require `DictSQLAlchemyMapper`
- `ValueObject` raw types must have a SQLAlchemy equivalent (common built-ins are supported; enums require `Enum`)

---

## 2. `FlatAutoSQLAlchemyMapper` — Zero Boilerplate, Single Table

Like `DictAutoSQLAlchemyMapper` but maps every field onto **one flat table** instead of using joins. Nested entity fields become prefixed columns (e.g. `engine.name` → `engine_name`) and `list[Entity]` fields are stored as a JSON column.

```python
from hike.ddd.providers.sqlalchemy import FlatAutoSQLAlchemyMapper, SQLAlchemyRepository

mapper = FlatAutoSQLAlchemyMapper(MotorBoat)
mapper.create_tables(engine)          # creates one table: motor_boats
repo = SQLAlchemyRepository(MotorBoat, mapper)
```

Share a `DeclarativeBase` with the rest of your app when you need `Base.metadata.create_all()`:

```python
from sqlalchemy.orm import DeclarativeBase
from hike.ddd.providers.sqlalchemy import FlatAutoSQLAlchemyMapper, SQLAlchemyRepository

class Base(DeclarativeBase): ...

mapper = FlatAutoSQLAlchemyMapper(MotorBoat, base=Base)
Base.metadata.create_all(engine)
repo = SQLAlchemyRepository(MotorBoat, mapper)
```

### What gets inferred

Given a `MotorBoat` aggregate with a nested `BoatEngine` entity, the generated table looks like this:

```
motor_boats
  id             UUID PRIMARY KEY
  name           VARCHAR
  price          FLOAT
  engine_id      UUID           ← nested BoatEngine.id
  engine_name    VARCHAR        ← nested BoatEngine.name
  engine_hp      FLOAT          ← nested BoatEngine.horsepower
  __hike_version INTEGER        ← managed by Hike (optimistic locking)
```

- `Field[ValueObject]` → scalar column (`String`, `Float`, `Integer`, `Uuid`, etc.)
- `Optional[Field[ValueObject]]` → `nullable=True` column
- `Field[Entity]` → all of the nested entity's fields are added with the field name as a prefix
- `Optional[Field[Entity]]` → same, but all nested columns are `nullable=True`
- `list[Entity]` → a single `JSON` column under the field name

### Choosing between `DictAutoSQLAlchemyMapper` and `FlatAutoSQLAlchemyMapper`

| | `DictAutoSQLAlchemyMapper` | `FlatAutoSQLAlchemyMapper` |
|---|---|---|
| Schema | One table per entity class | One table for the entire aggregate |
| Querying nested fields | SQL JOINs | Simple column lookups |
| Best for | Deep or shared entity hierarchies | Simple aggregates with few nested fields |
| `list[Entity]` | Stored in a child table (relational) | Stored as a JSON column |

---

## 3. `DictSQLAlchemyMapper` — Explicit ORM Models

Write your own SQLAlchemy ORM model classes (one per entity) and pass a `{EntityClass: ModelClass}` dict. You control column types, names, indexes, and constraints. Hike adds what it needs for safe writes automatically.

```python
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped, relationship
from sqlalchemy import ForeignKey
from hike.ddd.providers.sqlalchemy import DictSQLAlchemyMapper, SQLAlchemyRepository

class Base(DeclarativeBase): ...

class EngineModel(Base):
    __tablename__ = "engines"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str]
    horsepower: Mapped[float]

class BoatModel(Base):
    __tablename__ = "boats"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str]
    price: Mapped[float]
    engine_id: Mapped[UUID] = mapped_column(ForeignKey("engines.id"))
    engine: Mapped[EngineModel] = relationship(EngineModel)

# Declare at module level — BEFORE metadata.create_all()
mapper = DictSQLAlchemyMapper({MotorBoat: BoatModel, BoatEngine: EngineModel})

Base.metadata.create_all(engine)
repo = SQLAlchemyRepository(MotorBoat, mapper)
```

> **Important**: Mappers must be instantiated **before** `metadata.create_all()`. Declare them at module level — not inside a fixture or function.

---

## 4. `FlatSQLAlchemyMapper` — Explicit Single Table

All aggregate and nested entity fields live on one ORM model. Nested field names are prefixed with the field name and a separator (default `"_"`):

```
engine.name    →  engine_name column
engine.id      →  engine_id column
engine.speed   →  engine_speed column
```

`list[Entity]` fields are stored as a JSON/JSONB column under the field name.

```python
class FlatBoatModel(Base):
    __tablename__ = "flat_boats"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str]
    price: Mapped[float]
    engine_id: Mapped[UUID]
    engine_name: Mapped[str]
    engine_horsepower: Mapped[float]

# Declare at module level — BEFORE metadata.create_all()
mapper = FlatSQLAlchemyMapper(FlatBoatModel)
# Custom separator: FlatSQLAlchemyMapper(FlatBoatModel, sep="__")

Base.metadata.create_all(engine)
repo = SQLAlchemyRepository(MotorBoat, mapper)
```

The ORM model must declare all domain columns yourself. This mapper is well-suited for simple aggregates where joins would be overkill.

---

## Optimistic Locking

All four mappers keep your concurrent writes safe automatically. If two requests load the same aggregate and both try to save changes, Hike detects the conflict and raises `OptimisticLockError` on the second write — no silent data loss.

```python
from hike.ddd.repository import OptimisticLockError

with uow:
    boat = uow.repo.get_one(boat_id)

# -- another process updates the same row in the meantime --

with uow:
    boat.update_price(Price(9_999))
    try:
        uow.repo.update(boat)           # conflict detected → OptimisticLockError
    except OptimisticLockError:
        # re-fetch and retry
        ...
```

This works out of the box with no extra configuration — just use `update()` and Hike handles the rest.

---

## Choosing the Right Mapper

```
Just getting started, or don't need custom schema?
  → SQLAlchemyRepository(MyAggregate)      # DictAutoSQLAlchemyMapper by default (relational)
  → FlatAutoSQLAlchemyMapper(MyAggregate)  # zero boilerplate, single flat table

Need full control over schema (indexes, constraints, column types)?
  → DictSQLAlchemyMapper             # relational, one ORM model per entity
  → FlatSQLAlchemyMapper             # single-table, bring your own ORM model
```

---

## Quick Reference

```python
from hike.ddd.providers.sqlalchemy import (
    SQLAlchemyRepository,       # default mapper: DictAutoSQLAlchemyMapper
    DictAutoSQLAlchemyMapper,   # auto schema, one table per entity (relational)
    FlatAutoSQLAlchemyMapper,   # auto schema, single flat table
    DictSQLAlchemyMapper,       # explicit ORM models, one per entity
    FlatSQLAlchemyMapper,       # explicit ORM model, single table
)

# Zero boilerplate — relational (default)
repo = SQLAlchemyRepository(MyAggregate)

# Zero boilerplate — flat
mapper = FlatAutoSQLAlchemyMapper(MyAggregate)
repo   = SQLAlchemyRepository(MyAggregate, mapper)

# Explicit ORM models — relational
mapper = DictSQLAlchemyMapper({MyAggregate: AggModel, MyEntity: EntityModel})
repo   = SQLAlchemyRepository(MyAggregate, mapper)

# Explicit ORM model — flat
mapper = FlatSQLAlchemyMapper(FlatModel)
repo   = SQLAlchemyRepository(MyAggregate, mapper)

# Share a DeclarativeBase with the rest of your app
class Base(DeclarativeBase): ...
mapper = DictAutoSQLAlchemyMapper(MyAggregate, base=Base)
Base.metadata.create_all(engine)
```

**Next Step**: See the **[Repositories & Unit of Work](repositories-uow.md)** guide to learn how to wire a mapper into a repository and wrap it in a transaction.
