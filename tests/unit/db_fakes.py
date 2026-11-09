"""Engine fake for repositories that read with explicit SQL.

It records every step of the read in ``events``: ``"connect"``, ``"BEGIN"``,
one ``(sql, params)`` tuple per statement, ``"COMMIT"`` or ``"ROLLBACK"`` and
``"close"``. Rows are served per SQL text, so one engine can answer several
different queries.
"""

from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from sqlalchemy import TextClause
from sqlalchemy.exc import DBAPIError

SET_TIMEOUT = "SET LOCAL statement_timeout = '5s'"


def wrapped(
    error: Exception,
    statement: str = "SELECT 1",
    params: Mapping[str, Any] | None = None,
) -> DBAPIError:
    """The driver error wrapped the way SQLAlchemy wraps it."""
    return DBAPIError.instance(statement, params or {}, error, psycopg.Error)


class FakeResult:
    def __init__(self, rows: Sequence[tuple]):
        self._rows = list(rows)

    def all(self) -> list[tuple]:
        return list(self._rows)


class FakeConnection:
    def __init__(self, engine: "FakeEngine"):
        self._engine = engine

    @contextmanager
    def begin(self):
        self._engine.events.append("BEGIN")
        try:
            yield
        except BaseException:
            self._engine.events.append("ROLLBACK")
            raise
        self._engine.events.append("COMMIT")

    def execute(self, statement, parameters=None):
        assert isinstance(statement, TextClause)
        sql = str(statement)
        self._engine.events.append((sql, parameters))
        if sql == SET_TIMEOUT:
            return FakeResult([])
        if self._engine.query_error is not None:
            raise self._engine.query_error
        return FakeResult(self._engine.rows_by_sql.get(sql, []))


class FakeEngine:
    """Serves ``rows_by_sql[sql]`` (empty when the SQL is not listed).

    ``connect_error`` is raised when a connection is checked out;
    ``query_error`` is raised by any statement after the timeout.
    """

    def __init__(
        self,
        rows_by_sql: Mapping[str, Iterable[tuple]] | None = None,
        *,
        connect_error: Exception | None = None,
        query_error: Exception | None = None,
    ):
        self.events: list[Any] = []
        self.rows_by_sql = {
            sql: list(rows) for sql, rows in (rows_by_sql or {}).items()
        }
        self.connect_error = connect_error
        self.query_error = query_error

    @contextmanager
    def connect(self):
        if self.connect_error is not None:
            raise self.connect_error
        self.events.append("connect")
        try:
            yield FakeConnection(self)
        finally:
            self.events.append("close")

    def queries(self) -> list[tuple[str, Any]]:
        """``(sql, params)`` of each statement other than the timeout."""
        return [
            event
            for event in self.events
            if isinstance(event, tuple) and event[0] != SET_TIMEOUT
        ]

    def executed_sql(self) -> list[str]:
        return [event[0] for event in self.events if isinstance(event, tuple)]
