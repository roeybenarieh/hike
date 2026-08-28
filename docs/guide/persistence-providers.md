# Persistence Providers

Hike ships four ready-to-use repository implementations. All four satisfy the same `IRepository[TId, TAggregate, TSession]` interface — your application code never changes when you switch backends.

---

## 🧪 InMemoryRepository — Tests & Prototyping

No extra install needed. The in-memory provider is part of the core `hike` package.

```python
from hike import UnitOfWork
from hike.persistence.providers.in_memory import InMemoryDBContext, InMemoryRepository
from uuid import UUID

# Setup — one-time
ctx  = InMemoryDBContext()
repo = InMemoryRepository[UUID, Order]()
uow  = UnitOfWork(ctx)

# Save
with uow(repo):
    repo.save(order)
    uow.commit()

# Load
with uow(repo):
    fetched = repo.get_one(order.id)
```

`InMemoryDBContext` snapshots committed state on `begin()`, so a rollback restores the previous snapshot — full transaction semantics without a database.

---

## 🏛️ SQLAlchemyRepository — Relational Databases

**Install**

```
pip install ihike[sqlalchemy]
```

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from hike import UnitOfWork
from hike.persistence.providers.sqlalchemy import (
    SQLAlchemyDBContext,
    SQLAlchemyRepository,
    DictAutoSQLAlchemyMapper,
)

# 1. Declare a shared Base so metadata.create_all works
class Base(DeclarativeBase): ...

# 2. Create engine and session factory
engine  = create_engine("postgresql+psycopg://user:pass@localhost/db")
factory = sessionmaker(bind=engine)

# 3. Build mapper and create tables (once at startup)
mapper = DictAutoSQLAlchemyMapper(Order, base=Base)
Base.metadata.create_all(engine)

# 4. Wire up repository and UoW
repo = SQLAlchemyRepository(Order, mapper)
ctx  = SQLAlchemyDBContext(factory)
uow  = UnitOfWork(ctx)

# Use
with uow(repo):
    repo.save(order)
    uow.commit()
```

`DictAutoSQLAlchemyMapper` infers ORM models from your aggregate's field annotations — no manual ORM boilerplate. For full control over table names, column types, and indexes, see **[SQLAlchemy Mappers](sqlalchemy-mappers.md)**.

**Supported databases**: PostgreSQL, MySQL 8+, MariaDB, SQLite, Oracle, and SQL Server 2025 (v17+). Other SQLAlchemy-compatible engines work for basic CRUD and filtering — regex specifications require one of the listed dialects.

---

## 🍃 PyMongoRepository — MongoDB

**Install**

```
pip install ihike[pymongo]
```

```python
from pymongo import MongoClient
from hike import UnitOfWork
from hike.persistence.providers.pymongo import PyMongoDBContext, PyMongoRepository

# 1. Connect
client = MongoClient(
    "mongodb://localhost:27017",
    directConnection=True,
    uuidRepresentation="standard",
)
collection = client["mydb"]["orders"]

# 2. Wire up
repo = PyMongoRepository(collection, Order)
ctx  = PyMongoDBContext(client)
uow  = UnitOfWork(ctx)

# Use
with uow(repo):
    repo.save(order)
    uow.commit()
```

!!! warning "Replica set required"
    `PyMongoDBContext` wraps writes in a MongoDB multi-document transaction (`ClientSession`). Standalone MongoDB does not support multi-document transactions — you need a **replica set** or **mongos** (sharded cluster). For local development, start a one-node replica set:

    ```
    mongod --replSet rs0 --dbpath /data/db
    mongo --eval "rs.initiate()"
    ```

Aggregates are serialized to flat MongoDB documents. `UUID` fields are stored using the standard UUID representation and round-trip transparently.

---

## ⚡ RedisRepository — Redis

**Install**

```
pip install ihike[redis]
```

```python
from redis import Redis
from hike import UnitOfWork
from hike.persistence.providers.redis import RedisDBContext, RedisRepository

# 1. Connect
client = Redis(host="localhost", port=6379)

# 2. Wire up
repo = RedisRepository(client, Order, key_prefix="orders")
ctx  = RedisDBContext(client)
uow  = UnitOfWork(ctx)

# Use
with uow(repo):
    repo.save(order)
    uow.commit()
```

`key_prefix` sets the Redis key namespace — each aggregate is stored under `{key_prefix}:{id}` as a JSON string.

**How transactions work**: write operations (`save`, `update`, `delete`, `upsert`) are queued into a Redis pipeline and executed atomically via `MULTI/EXEC` on commit. Read operations (`get_one`, `get_many`) bypass the pipeline and read directly from Redis.

!!! note "`get_many` evaluates specifications in memory"
    `get_many` scans every key matching `{key_prefix}:*` and evaluates the specification in Python. This is the correct approach for Redis: RediSearch indices require a static field schema declared at creation time, which is fundamentally at odds with the dynamic, composable specification pattern — you cannot know at repository initialisation which fields will be queried, or with which operators. For small-to-medium datasets this is fine; if you need server-side filtering at scale, use a relational or document database instead.

---

## Regex filtering

All providers support `Field.matches(pattern)` via `RegexSpecification`. Patterns are validated at construction time — invalid syntax raises `re2.error` immediately, before any query reaches the database.

### Regex standard

Hike uses **[google-re2](https://github.com/google/re2)** as the reference engine. Every pattern must be a valid RE2 expression. RE2 accepts most POSIX ERE syntax plus several common Perl extensions (`\d`, `\w`, `\s`, `\b`). It deliberately rejects constructs that require backtracking:

| Construct | Status | Alternative |
| :--- | :--- | :--- |
| `[[:alpha:]]`, `[[:digit:]]` | ✅ Supported | — |
| `\d`, `\w`, `\s`, `\b` | ✅ Supported | — |
| `.` (dot) | ✅ Supported | Does **not** match `\n` |
| `(?i)` inline flags | ✅ Supported | Use `case_insensitive=True` arg instead |
| `(?=…)` lookahead | ❌ Rejected | No alternative |
| `\1` backreferences | ❌ Rejected | No alternative |

```python
# Valid — validated immediately by re2
sku_spec    = Product.sku.matches(r"^[A-Z]{2}-\d{4}$")
name_spec   = Product.name.matches(r"pro", case_insensitive=True)
alpha_spec  = Product.name.matches(r"^[[:alpha:]]+$")

# Invalid — raises re2.error at construction time (before any DB call)
Product.name.matches(r"(?=sale)")   # lookahead
Product.name.matches(r"(.)\1")      # backreference
```

### POSIX character classes — ASCII-only across all providers

POSIX named classes (`[[:alpha:]]`, `[[:upper:]]`, `[[:lower:]]`, `[[:alnum:]]`) are **ASCII-only** in every provider. This matches RE2's native behaviour and means `[[:alpha:]]` matches `[A-Za-z]` — not Unicode letters like `é` or `ñ`.

Providers whose database engine would otherwise treat these classes as Unicode-aware (MySQL ICU, MariaDB PCRE, Oracle, PostgreSQL UTF-8) automatically receive a rewritten pattern (`[[:alpha:]]` → `[A-Za-z]`) so results are identical across all backends.

### Per-provider support

| Provider | Database | Mechanism | Notes |
| :--- | :--- | :--- | :--- |
| **SQLAlchemy** | PostgreSQL | `~` / `~*` operators | `.` rewritten to `[^\n]` — PostgreSQL's `.` matches newlines by default |
| **SQLAlchemy** | MySQL 8+ | `REGEXP_LIKE(col, pat, flags)` | Requires MySQL ≥ 8.0 |
| **SQLAlchemy** | MariaDB | `col REGEXP pat` / `REGEXP BINARY` | `(?i)` prefix for case-insensitive |
| **SQLAlchemy** | SQLite | Python UDFs `REGEXP` / `IREGEXP` | UDFs must be registered on each connection (see below) |
| **SQLAlchemy** | SQL Server | `REGEXP_LIKE(col, pat, flags)` | Requires SQL Server 2025 (major version ≥ 17) |
| **SQLAlchemy** | Oracle | `REGEXP_LIKE(col, pat, flags)` | `'c'` / `'i'` match parameter |
| **PyMongo** | MongoDB | `{ $regex: pat, $options: … }` | PCRE2 engine; ASCII-only by default |
| **InMemory** | — | `re2.search()` / `re2.fullmatch()` | Reference behaviour |
| **Redis** | — | `re2.search()` in-memory scan | Scans all keys; no server-side filtering |

### SQLite — registering UDFs

SQLite has no built-in regex engine. You must register two Python functions (`REGEXP` and `IREGEXP`) backed by RE2 before using `matches()`. Do this once when the engine is created:

```python
import re2
from sqlalchemy import event

def _register_regexp_udfs(dbapi_connection, _connection_record):
    _icase = re2.Options()
    _icase.case_sensitive = False

    def regexp(pattern: str, value: str) -> bool:
        try:
            return re2.search(pattern, value) is not None
        except Exception:
            return False

    def iregexp(pattern: str, value: str) -> bool:
        try:
            return re2.search(pattern, value, _icase) is not None
        except Exception:
            return False

    dbapi_connection.create_function("REGEXP", 2, regexp)
    dbapi_connection.create_function("IREGEXP", 2, iregexp)

engine = create_engine("sqlite:///mydb.db", connect_args={"check_same_thread": False})
event.listen(engine, "connect", _register_regexp_udfs)
```

!!! note "WAL mode recommended"
    If you also use `watch()`, enable WAL mode so the polling thread can read rows committed by the main session:
    ```python
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.commit()
    ```

---

## Switching providers

Because every provider implements the same `IRepository` interface, switching is a one-line change in your setup code. Business logic and domain tests are unaffected:

```python
# Development / tests
repo = InMemoryRepository[UUID, Order]()
ctx  = InMemoryDBContext()

# Production (swap these two lines, nothing else changes)
repo = SQLAlchemyRepository(Order, mapper)
ctx  = SQLAlchemyDBContext(factory)

uow = UnitOfWork(ctx)  # same for both
```
