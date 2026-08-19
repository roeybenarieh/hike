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

**Supported databases**: PostgreSQL, SQLite, MySQL, MariaDB, and any other SQLAlchemy-compatible engine.

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
