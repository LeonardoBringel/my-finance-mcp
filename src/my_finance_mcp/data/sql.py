"""Bounded SELECT with the standard database error mapping."""

import logging
from typing import Any

from sqlalchemy import Engine, Row, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from my_finance_mcp.data.db import DatabaseUnavailableError

logger = logging.getLogger(__name__)


def read_all(
    engine: Engine, sql: str, params: dict[str, Any], table: str
) -> list[Row]:
    """Every row of sql, read in its own transaction with a 5 s timeout."""
    try:
        with engine.connect() as conn, conn.begin():
            # SET LOCAL only lasts for the transaction the SELECT runs in.
            conn.execute(text("SET LOCAL statement_timeout = '5s'"))
            # Values go as bind parameters, never into the SQL text.
            return list(conn.execute(text(sql), params).all())
    except ProgrammingError as exc:
        # Only the driver reason: SQLAlchemy's own text carries the parameters.
        logger.warning("Não foi possível ler %s: %s", table, exc.orig)
        raise DatabaseUnavailableError() from exc
    except (DBAPIError, PoolTimeoutError) as exc:
        # Statement timeouts arrive here too: psycopg's QueryCanceled is an
        # OperationalError.
        raise DatabaseUnavailableError() from exc
