"""Explicit SQL reads of the users table."""

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, Row, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from my_finance_mcp.data.db import DatabaseUnavailableError

logger = logging.getLogger(__name__)

SELECT_TOKEN_VERSION = "SELECT token_version FROM users WHERE id = :user_id"
SELECT_BY_USERNAME_HASH = (
    "SELECT id, token_version FROM users WHERE username_hash = :username_hash"
)


@dataclass(frozen=True)
class UserVersion:
    user_id: int
    token_version: int


def get_token_version(engine: Engine, user_id: int) -> int | None:
    row = _read_one(engine, SELECT_TOKEN_VERSION, {"user_id": user_id})
    if row is None:
        return None
    return row[0]


def find_user_by_username_hash(
    engine: Engine, username_hash: str
) -> UserVersion | None:
    row = _read_one(
        engine, SELECT_BY_USERNAME_HASH, {"username_hash": username_hash}
    )
    if row is None:
        return None
    return UserVersion(user_id=row[0], token_version=row[1])


def _read_one(engine: Engine, sql: str, params: dict[str, Any]) -> Row | None:
    try:
        with engine.connect() as conn, conn.begin():
            # SET LOCAL only lasts for the transaction the SELECT runs in.
            conn.execute(text("SET LOCAL statement_timeout = '5s'"))
            # Values go as bind parameters, never into the SQL text.
            return conn.execute(text(sql), params).first()
    except ProgrammingError as exc:
        logger.warning("Não foi possível ler users: %s", exc.orig)
        raise DatabaseUnavailableError() from exc
    except (DBAPIError, PoolTimeoutError) as exc:
        # Statement timeouts arrive here too: psycopg's QueryCanceled is an
        # OperationalError.
        raise DatabaseUnavailableError() from exc
