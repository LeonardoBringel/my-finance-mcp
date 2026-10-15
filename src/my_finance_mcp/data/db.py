"""Read-only engine and alembic_version reader."""

import logging
import threading
import time
from collections.abc import Callable

from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from my_finance_mcp.config import Settings

logger = logging.getLogger(__name__)

SUPPORTED_SCHEMA_REVISIONS: tuple[str, ...] = ("0009",)
READY_CACHE_SECONDS = 30


class DatabaseUnavailableError(Exception):
    """The database did not answer in time (refused, timed out, canceled)."""


class SchemaUnreadableError(Exception):
    """alembic_version could not be read (missing table or grant)."""


def create_db_engine(settings: Settings) -> Engine:
    url = URL.create(
        "postgresql+psycopg",
        username=settings.DB_USER,
        password=settings.DB_PASSWORD.get_secret_value(),
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        database=settings.DB_NAME,
    )
    return create_engine(
        url,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=0,
        pool_timeout=2,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 2,
            "options": "-c default_transaction_read_only=on",
        },
    )


def read_schema_revisions(engine: Engine) -> list[str]:
    try:
        with engine.connect() as conn, conn.begin():
            # SET LOCAL only lasts for the transaction the SELECT runs in.
            conn.execute(text("SET LOCAL statement_timeout = '5s'"))
            result = conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
            return list(result.scalars())
    except ProgrammingError as exc:
        reason = str(exc.orig)
        logger.warning("Não foi possível ler alembic_version: %s", reason)
        raise SchemaUnreadableError(reason) from exc
    except (DBAPIError, PoolTimeoutError) as exc:
        # Statement timeouts arrive here too: psycopg's QueryCanceled is an
        # OperationalError.
        raise DatabaseUnavailableError() from exc


class CachedRevisionReader:
    """Keeps the last read, success or database failure, for ttl_seconds."""

    def __init__(
        self,
        reader: Callable[[], list[str]],
        ttl_seconds: float = READY_CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.reader = reader
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._lock = threading.Lock()
        self._last: tuple[float, list[str] | Exception] | None = None

    def __call__(self) -> list[str]:
        with self._lock:
            last = self._last
        if last is None or self.clock() - last[0] >= self.ttl_seconds:
            # The lock is not held here so a slow database never queues
            # other callers behind it.
            read_at = self.clock()
            try:
                outcome = list(self.reader())
            except (DatabaseUnavailableError, SchemaUnreadableError) as exc:
                outcome = exc
            last = (read_at, outcome)
            with self._lock:
                self._last = last
        outcome = last[1]
        if isinstance(outcome, Exception):
            # Reset the traceback so repeated raises do not keep growing it.
            raise outcome.with_traceback(None)
        return list(outcome)
