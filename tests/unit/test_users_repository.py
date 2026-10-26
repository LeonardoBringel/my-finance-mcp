"""Token version and username hash lookups on users."""

import logging
from contextlib import contextmanager

import psycopg
import pytest
from sqlalchemy import TextClause
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from my_finance_mcp.data.db import DatabaseUnavailableError
from my_finance_mcp.data.repositories.users_repository import (
    UserVersion,
    find_user_by_username_hash,
    get_token_version,
)

LOGGER_NAME = "my_finance_mcp.data.repositories.users_repository"
SET_TIMEOUT = "SET LOCAL statement_timeout = '5s'"
SELECT_TOKEN_VERSION = "SELECT token_version FROM users WHERE id = :user_id"
SELECT_BY_USERNAME_HASH = (
    "SELECT id, token_version FROM users WHERE username_hash = :username_hash"
)
USERNAME_HASH = (
    "58bdcfa5c47eb8f88d243d53939c2f5e7600bcfa9c8ad9d509e7c601917f4d95"
)


def wrapped(error: Exception) -> DBAPIError:
    # Same wrapping SQLAlchemy applies to errors raised by the driver.
    return DBAPIError.instance(SELECT_TOKEN_VERSION, {}, error, psycopg.Error)


class _Result:
    def __init__(self, rows, events):
        self._rows = rows
        self._events = events

    def first(self):
        self._events.append("fetch")
        return self._rows[0] if self._rows else None


class _Connection:
    def __init__(self, events, rows, error):
        self._events = events
        self._rows = rows
        self._error = error

    @contextmanager
    def begin(self):
        self._events.append("BEGIN")
        try:
            yield
        except BaseException:
            self._events.append("ROLLBACK")
            raise
        self._events.append("COMMIT")

    def execute(self, statement, parameters=None):
        assert isinstance(statement, TextClause)
        self._events.append((str(statement), parameters))
        if self._error is not None and str(statement) != SET_TIMEOUT:
            raise self._error
        return _Result(self._rows, self._events)


class _Engine:
    def __init__(self, rows=(), error=None, connect_error=None):
        self.events = []
        self._rows = list(rows)
        self._error = error
        self._connect_error = connect_error

    @contextmanager
    def connect(self):
        if self._connect_error is not None:
            raise self._connect_error
        self.events.append("connect")
        try:
            yield _Connection(self.events, self._rows, self._error)
        finally:
            self.events.append("close")

    def executed_sql(self):
        return [event[0] for event in self.events if isinstance(event, tuple)]


LOOKUPS = [
    pytest.param(lambda engine: get_token_version(engine, 42), id="by_id"),
    pytest.param(
        lambda engine: find_user_by_username_hash(engine, USERNAME_HASH),
        id="by_username_hash",
    ),
]


def test_token_version_sets_timeout_then_selects_in_one_transaction():
    engine = _Engine(rows=[(3,)])

    assert get_token_version(engine, 42) == 3
    assert engine.events == [
        "connect",
        "BEGIN",
        (SET_TIMEOUT, None),
        (SELECT_TOKEN_VERSION, {"user_id": 42}),
        "fetch",
        "COMMIT",
        "close",
    ]


def test_token_version_is_the_int_of_the_row():
    engine = _Engine(rows=[(0,)])

    version = get_token_version(engine, 42)

    assert version == 0
    assert type(version) is int


def test_username_hash_sets_timeout_then_selects_in_one_transaction():
    engine = _Engine(rows=[(42, 7)])

    user = find_user_by_username_hash(engine, USERNAME_HASH)

    assert user == UserVersion(user_id=42, token_version=7)
    assert engine.events == [
        "connect",
        "BEGIN",
        (SET_TIMEOUT, None),
        (SELECT_BY_USERNAME_HASH, {"username_hash": USERNAME_HASH}),
        "fetch",
        "COMMIT",
        "close",
    ]


@pytest.mark.parametrize("lookup", LOOKUPS)
def test_missing_row_means_none(lookup):
    engine = _Engine(rows=[])

    assert lookup(engine) is None
    assert engine.events[-2:] == ["COMMIT", "close"]


@pytest.mark.parametrize(
    "lookup,value",
    [
        (lambda engine: get_token_version(engine, 987654), "987654"),
        (
            lambda engine: find_user_by_username_hash(engine, USERNAME_HASH),
            USERNAME_HASH,
        ),
    ],
)
def test_parameter_value_never_appears_in_sql_text(lookup, value):
    engine = _Engine(rows=[])

    lookup(engine)

    sql = engine.executed_sql()
    assert len(sql) == 2
    assert all(value not in statement for statement in sql)


@pytest.mark.parametrize("lookup", LOOKUPS)
@pytest.mark.parametrize(
    "error",
    [
        wrapped(psycopg.OperationalError("connection timeout expired")),
        PoolTimeoutError("QueuePool limit of size 7 overflow 0 reached"),
    ],
)
def test_connect_failure_means_database_unavailable(lookup, error):
    engine = _Engine(connect_error=error)

    with pytest.raises(DatabaseUnavailableError) as exc_info:
        lookup(engine)

    assert exc_info.value.__cause__ is error


@pytest.mark.parametrize("lookup", LOOKUPS)
@pytest.mark.parametrize(
    "error",
    [
        wrapped(
            psycopg.errors.QueryCanceled(
                "canceling statement due to statement timeout"
            )
        ),
        wrapped(psycopg.OperationalError("server closed the connection")),
        wrapped(psycopg.InterfaceError("connection already closed")),
        PoolTimeoutError("QueuePool limit of size 7 overflow 0 reached"),
    ],
)
def test_query_failure_means_database_unavailable(lookup, error, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    engine = _Engine(error=error)

    with pytest.raises(DatabaseUnavailableError) as exc_info:
        lookup(engine)

    assert exc_info.value.__cause__ is error
    assert "ROLLBACK" in engine.events
    assert [r for r in caplog.records if r.name == LOGGER_NAME] == []


@pytest.mark.parametrize("lookup", LOOKUPS)
@pytest.mark.parametrize(
    "error,reason",
    [
        (
            psycopg.errors.UndefinedTable('relation "users" does not exist'),
            'relation "users" does not exist',
        ),
        (
            psycopg.errors.InsufficientPrivilege(
                "permission denied for table users"
            ),
            "permission denied for table users",
        ),
    ],
)
def test_programming_error_logs_warning_and_means_unavailable(
    lookup, error, reason, caplog
):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    driver_error = wrapped(error)
    engine = _Engine(error=driver_error)

    with pytest.raises(DatabaseUnavailableError) as exc_info:
        lookup(engine)

    assert exc_info.value.__cause__ is driver_error
    records = [r for r in caplog.records if r.name == LOGGER_NAME]
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (logging.WARNING, f"Não foi possível ler users: {reason}")
    ]
