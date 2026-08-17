# Getting Started with Hike

Welcome! This guide will walk you through installing Hike and building your very first domain model—**step by step, with zero prior DDD knowledge required**.

## 1. System Requirements

| Requirement | Version | Why it matters |
|-------------|---------|----------------|
| **Python** | `>= 3.14` | Hike uses modern type‑hinting features that require Python 3.14+. |
| **pip** | latest | The standard Python package installer, included with Python. |

> **Check your Python version:**
> ```bash
> python --version   # must be 3.14 or higher
> ```
> If it prints `3.13` or lower, download a newer release from [python.org](https://www.python.org/downloads/).

## 2. Install Hike

### a) Basic installation

```bash
pip install hike
```

### b) Installing optional database connectors

If you plan to persist data to a database, add the extra for each backend:

```bash
# SQLAlchemy (relational DBs — PostgreSQL, SQLite, MySQL, …)
pip install "hike[sqlalchemy]"

# PyMongo (MongoDB)
pip install "hike[pymongo]"

# Redis (key‑value store)
pip install "hike[redis]"
```

### c) Install everything at once

```bash
pip install "hike[all]"
```

## 3. Verify the Installation

Open a Python REPL and run:

```python
import hike
print("Hike version:", hike.__version__)
```

You should see something like:

```
Hike version: 0.1.0
```

If you see no errors, you're ready to move on to the fun part: **building your first domain model!**

---

Happy coding! 🚀
