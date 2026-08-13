# Getting Started with Hike

Welcome! This guide will walk you through installing Hike and building your very first domain model—**step by step, with zero prior DDD knowledge required**.

## 1. System Requirements

| Requirement | Version | Why it matters |
|-------------|---------|----------------|
| **Python** | `>= 3.14` | Hike uses modern type‑hinting features that require Python 3.14+. |
| **Package Manager** | `uv` (recommended) | A fast, zero‑config installer that handles transitive dependencies automatically. |

> **If you don’t have `uv` yet:** Install it in one line:
> ```bash
> curl -sSf https://astral.sh/uv/install.py | sh
> ```

## 2. Install Hike

### a) Basic Installation (quickest way)

```bash
uv sync
```

That command reads a `pyproject.toml` file (which already contains `hike` as a dependency) and installs Hike and any other declared dependencies.

### b) Installing optional database connectors

If you plan to persist data to a database, you’ll need an extra for each backend:

```bash
# SQLAlchemy (relational DBs)
uv pip install -e ".[sqlalchemy]"

# PyMongo (MongoDB)
uv pip install -e ".[pymongo]"

# Redis (key‑value store)
uv pip install -e ".[redis]"
```

### c) Install everything at once

All extra connectors under a single flag:

```bash
uv pip install -e ".[all]"
```

## 3. Verify the Installation

After installation, open a Python REPL and run:

```python
import hike
print("Hike version:", hike.__version__)
```

You should see something like:

```
Hike version: 0.1.0
```

If you see no errors, you’re ready to move on to the fun part: **building your first domain model!**

---

## 4. Next Steps

- **Learn the basics** – Read our [Value Objects](value-objects.md) and [Entities & Aggregates](entities-aggregates.md) guides to understand the core building blocks.
- **Persist data** – Check out the [Repositories & Unit of Work](repositories-uow.md) page to see how to store and retrieve your objects safely.
- **Write reusable business rules** – See the [Specifications](specifications.md) page for a gentle intro to composable query filters.

Happy coding! 🚀
