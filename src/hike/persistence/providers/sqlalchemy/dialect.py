from __future__ import annotations

from enum import Enum

from hike.persistence.repository import UnsupportedDialectError


class SupportedDialects(str, Enum):
    """Database dialects supported by the SQLAlchemy repository provider.

    Pass ``SupportedDialects.parse(connection.dialect.name)`` to resolve and
    validate a live connection's dialect.  Unknown dialect names raise
    :class:`~hike.persistence.repository.UnsupportedDialectError` immediately.

    SQL Server (``MSSQL``) requires version 17+ (SQL Server 2025) for regex
    specifications; older versions raise ``UnsupportedDialectError`` when a
    :class:`~hike.specifications.specs.RegexSpecification` is evaluated.
    """

    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    MARIADB = "mariadb"
    ORACLE = "oracle"
    SQLITE = "sqlite"
    MSSQL = "mssql"

    @classmethod
    def parse(cls, name: str) -> SupportedDialects:
        """Resolve *name* to a ``SupportedDialects`` member, raising on unknown values."""
        try:
            return cls(name)
        except ValueError:
            supported = ", ".join(m.value for m in cls)
            raise UnsupportedDialectError(
                f"Dialect {name!r} is not supported by hike. "
                f"Supported dialects: {supported}."
            ) from None
