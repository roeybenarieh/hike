"""PyMongo provider for hike DDD building blocks.

Requires the ``pymongo`` extra::

    pip install hike[pymongo]
"""

from __future__ import annotations

try:
    import pymongo  # noqa: F401  # pyright: ignore[reportUnusedImport]
except ImportError as _exc:
    raise ImportError(
        "The pymongo extra is required for this provider. "
        "Install it with: pip install hike[pymongo]"
    ) from _exc

from hike.ddd.providers.pymongo.db_context import PyMongoDBContext
from hike.ddd.providers.pymongo.repository import PyMongoRepository

__all__ = [
    "PyMongoDBContext",
    "PyMongoRepository",
]
