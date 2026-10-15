"""Read-only engine, schema revision reader and its cache."""

import base64
import inspect
import logging
import time
from contextlib import contextmanager

import psycopg
import pytest
from sqlalchemy import TextClause
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from my_finance_mcp.config import Settings
from my_finance_mcp.data import db
from my_finance_mcp.data.db import (
    READY_CACHE_SECONDS,
    SUPPORTED_SCHEMA_REVISIONS,
    CachedRevisionReader,
    DatabaseUnavailableError,
    SchemaUnreadableError,
    create_db_engine,
    read_schema_revisions,
)

READ_ONLY_OPTION = "-c default_transaction_read_only=on"
SELECT_REVISIONS = "SELECT version_num FROM alembic_version"

SETTINGS_VALUES = {
    "DB_HOST": "db.internal",
    "DB_PORT": 6543,
    "DB_NAME": "finance",
    "DB_USER": "my_finance_mcp",
    "DB_PASSWORD": "ZZSECRET-db-pass",
    "FERNET_KEY": base64.urlsafe_b64encode(
        b"ZZSECRET-fernet-key-32-bytes-ok!"
    ).decode(),
    "MCP_TOKEN_SECRET": "ZZSECRET-token-secret-at-least-32-bytes",
    "MCP_PUBLIC_URL": "https://finance-mcp.x.ts.net/mcp",
    "MCP_ALLOWED_HOSTS": ["finance-mcp.x.ts.net"],
    "DB_POOL_SIZE": 7,
}


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings():
    return Settings(_env_file=None, **SETTINGS_VALUES)


def wrapped(error: Exception) -> DBAPIError:
    # Same wrapping SQLAlchemy applies to errors raised by the driver.
    return DBAPIError.instance(SELECT_REVISIONS, {}, error, psycopg.Error)


def test_supported_schema_revisions():
    assert SUPPORTED_SCHEMA_REVISIONS == ("0009",)


def test_engine_options_are_read_only_bounded_and_pre_pinged(
    settings, monkeypatch
):
    calls = []
    sentinel = object()

    def fake_create_engine(*args, **kwargs):
        calls.append((args, kwargs))
        return sentinel

    monkeypatch.setattr(db, "create_engine", fake_create_engine)

    assert create_db_engine(settings) is sentinel
    assert len(calls) == 1
    (url,), kwargs = calls[0]
    assert url.drivername == "postgresql+psycopg"
    assert url.host == "db.internal"
    assert url.port == 6543
    assert url.database == "finance"
    assert url.username == "my_finance_mcp"
    assert url.password == "ZZSECRET-db-pass"
    assert kwargs == {
        "pool_size": 7,
        "max_overflow": 0,
        "pool_timeout": 2,
        "pool_pre_ping": True,
        "connect_args": {
            "connect_timeout": 2,
            "options": READ_ONLY_OPTION,
        },
    }


class _ConnectAttempt(Exception):
    pass


def test_engine_creation_does_not_connect(settings, monkeypatch):
    attempts = []

    def fake_connect(*args, **kwargs):
        attempts.append(kwargs)
        raise _ConnectAttempt

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    unreachable = settings.model_copy(update={"DB_HOST": "unreachable.invalid"})

    engine = create_db_engine(unreachable)

    assert attempts == []
    # The first real checkout reaches the driver with the read-only options.
    with pytest.raises(_ConnectAttempt):
        engine.connect()
    assert len(attempts) == 1
    assert attempts[0]["host"] == "unreachable.invalid"
    assert attempts[0]["connect_timeout"] == 2
    assert attempts[0]["options"] == READ_ONLY_OPTION


class _Result:
    def __init__(self, rows, events):
        self._rows = rows
        self._events = events

    def scalars(self):
        self._events.append("fetch")
        return iter(self._rows)


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

    def execute(self, statement, *args, **kwargs):
        assert isinstance(statement, TextClause)
        self._events.append(str(statement))
        if self._error is not None and str(statement) == SELECT_REVISIONS:
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


@pytest.mark.parametrize("rows", [["0009"], ["0009", "0010"], []])
def test_reader_sets_timeout_then_selects_in_one_transaction(rows):
    engine = _Engine(rows=rows)

    assert read_schema_revisions(engine) == rows
    assert engine.events == [
        "connect",
        "BEGIN",
        "SET LOCAL statement_timeout = '5s'",
        SELECT_REVISIONS,
        "fetch",
        "COMMIT",
        "close",
    ]


@pytest.mark.parametrize(
    "error",
    [
        wrapped(psycopg.OperationalError("connection timeout expired")),
        PoolTimeoutError("QueuePool limit of size 7 overflow 0 reached"),
    ],
)
def test_connect_failure_means_database_unavailable(error):
    engine = _Engine(connect_error=error)

    with pytest.raises(DatabaseUnavailableError) as exc_info:
        read_schema_revisions(engine)

    assert exc_info.value.__cause__ is error


@pytest.mark.parametrize(
    "error",
    [
        wrapped(
            psycopg.errors.QueryCanceled(
                "canceling statement due to statement timeout"
            )
        ),
        wrapped(psycopg.OperationalError("server closed the connection")),
        wrapped(psycopg.InternalError("internal error")),
        wrapped(psycopg.InterfaceError("connection already closed")),
    ],
)
def test_query_failure_means_database_unavailable(error):
    engine = _Engine(error=error)

    with pytest.raises(DatabaseUnavailableError) as exc_info:
        read_schema_revisions(engine)

    assert exc_info.value.__cause__ is error


@pytest.mark.parametrize(
    "error,reason",
    [
        (
            psycopg.errors.UndefinedTable(
                'relation "alembic_version" does not exist'
            ),
            'relation "alembic_version" does not exist',
        ),
        (
            psycopg.errors.InsufficientPrivilege(
                "permission denied for table alembic_version"
            ),
            "permission denied for table alembic_version",
        ),
    ],
)
def test_unreadable_table_means_schema_unreadable(error, reason, caplog):
    caplog.set_level(logging.WARNING, logger="my_finance_mcp.data.db")
    engine = _Engine(error=wrapped(error))

    with pytest.raises(SchemaUnreadableError) as exc_info:
        read_schema_revisions(engine)

    assert str(exc_info.value) == reason
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name == "my_finance_mcp.data.db"
        and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert reason in warnings[0]


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class _Reader:
    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def __call__(self):
        outcome = self._outcomes[min(self.calls, len(self._outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return list(outcome)


def test_ready_cache_window_is_30_seconds():
    assert READY_CACHE_SECONDS == 30


def test_first_call_reads_and_result_is_reused_under_30_seconds():
    clock = _Clock()
    reader = _Reader(["0009"], ["0010"])
    cached = CachedRevisionReader(reader, clock=clock)

    assert cached() == ["0009"]
    assert reader.calls == 1
    clock.now += 29.9
    assert cached() == ["0009"]
    assert reader.calls == 1


def test_read_again_at_exactly_30_seconds():
    clock = _Clock()
    reader = _Reader(["0009"], ["0010"])
    cached = CachedRevisionReader(reader, clock=clock)

    cached()
    clock.now += 30
    assert cached() == ["0010"]
    assert reader.calls == 2
    clock.now += 29.9
    assert cached() == ["0010"]
    assert reader.calls == 2


def test_custom_ttl_is_honoured():
    clock = _Clock()
    reader = _Reader(["0009"])
    cached = CachedRevisionReader(reader, ttl_seconds=5, clock=clock)

    cached()
    clock.now += 4.9
    cached()
    assert reader.calls == 1
    clock.now += 0.1
    cached()
    assert reader.calls == 2


@pytest.mark.parametrize(
    "failure",
    [
        DatabaseUnavailableError(),
        SchemaUnreadableError("permission denied for table alembic_version"),
    ],
)
def test_database_failure_is_cached_until_30_seconds_then_replaced(failure):
    clock = _Clock()
    reader = _Reader(failure, ["0009"])
    cached = CachedRevisionReader(reader, clock=clock)

    with pytest.raises(type(failure)) as first:
        cached()
    assert first.value is failure
    # The database is back at 10 s, but the failure is still served.
    for elapsed in (10, 29.9):
        clock.now = 1000.0 + elapsed
        with pytest.raises(type(failure)):
            cached()
        assert reader.calls == 1
    clock.now = 1000.0 + 30
    assert cached() == ["0009"]
    assert reader.calls == 2
    clock.now += 29.9
    assert cached() == ["0009"]
    assert reader.calls == 2


def test_other_reader_errors_are_not_cached():
    clock = _Clock()
    reader = _Reader(RuntimeError("bug"), ["0009"])
    cached = CachedRevisionReader(reader, clock=clock)

    with pytest.raises(RuntimeError):
        cached()
    assert cached() == ["0009"]
    assert reader.calls == 2


def test_mutating_returned_list_does_not_change_cache():
    clock = _Clock()
    reader = _Reader(["0009"])
    cached = CachedRevisionReader(reader, clock=clock)

    cached().append("0010")
    cached().clear()

    assert cached() == ["0009"]
    assert reader.calls == 1


def test_defaults_are_30_seconds_on_the_monotonic_clock():
    parameters = inspect.signature(CachedRevisionReader).parameters

    assert parameters["ttl_seconds"].default == READY_CACHE_SECONDS
    assert parameters["clock"].default is time.monotonic
