"""SQLAlchemy provider for hike DDD building blocks.

Requires the ``sqlalchemy`` extra::

    pip install hike[sqlalchemy]
"""

from __future__ import annotations

try:
    import sqlalchemy  # noqa: F401  # pyright: ignore[reportUnusedImport]
except ImportError as _exc:
    raise ImportError(
        "The sqlalchemy extra is required for this provider. "
        "Install it with: pip install hike[sqlalchemy]"
    ) from _exc

from hike.ddd.providers.sqlalchemy.db_context import SQLAlchemyDBContext
from hike.ddd.providers.sqlalchemy.mappers import (
    DictAutoSQLAlchemyMapper,
    DictSQLAlchemyMapper,
    FlatAutoSQLAlchemyMapper,
    FlatSQLAlchemyMapper,
)
from hike.ddd.providers.sqlalchemy.repository import SQLAlchemyRepository
from hike.ddd.providers.sqlalchemy.visitor import ISQLAlchemyMapper

__all__ = [
    "DictAutoSQLAlchemyMapper",
    "DictSQLAlchemyMapper",
    "FlatAutoSQLAlchemyMapper",
    "FlatSQLAlchemyMapper",
    "ISQLAlchemyMapper",
    "SQLAlchemyDBContext",
    "SQLAlchemyRepository",
]
