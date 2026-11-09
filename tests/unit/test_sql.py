"""Bounded SELECT with the standard database error mapping."""

import logging

import psycopg
import pytest
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from my_finance_mcp.data.db import DatabaseUnavailableError
from my_finance_mcp.data.sql import read_all
from tests.unit.db_fakes import SET_TIMEOUT, FakeEngine, wrapped

LOGGER_NAME = "my_finance_mcp.data.sql"
SELECT = "SELECT id, name FROM categories WHERE user_id = :user_id"
PARAMS = {"user_id": 987654}


def test_sets_timeout_then_selects_in_one_transaction():
    rows = [(1, "token-a"), (2, "token-b")]
    engine = FakeEngine({SELECT: rows})

    result = read_all(engine, SELECT, PARAMS, "categories")

    assert result == rows
    assert type(result) is list
    assert engine.events == [
        "connect",
        "BEGIN",
        (SET_TIMEOUT, None),
        (SELECT, {"user_id": 987654}),
        "COMMIT",
        "close",
    ]


def test_no_rows_is_an_empty_list():
    engine = FakeEngine()

    assert read_all(engine, SELECT, PARAMS, "categories") == []
    assert engine.events[-2:] == ["COMMIT", "close"]


@pytest.mark.parametrize(
    "error",
    [
        wrapped(psycopg.OperationalError("connection timeout expired")),
        PoolTimeoutError("QueuePool limit of size 7 overflow 0 reached"),
    ],
)
def test_connect_failure_means_database_unavailable(error):
    engine = FakeEngine(connect_error=error)

    with pytest.raises(DatabaseUnavailableError) as exc_info:
        read_all(engine, SELECT, PARAMS, "categories")

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
        wrapped(psycopg.InterfaceError("connection already closed")),
        PoolTimeoutError("QueuePool limit of size 7 overflow 0 reached"),
    ],
)
def test_query_failure_means_database_unavailable_without_log(error, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    engine = FakeEngine(query_error=error)

    with pytest.raises(DatabaseUnavailableError) as exc_info:
        read_all(engine, SELECT, PARAMS, "categories")

    assert exc_info.value.__cause__ is error
    assert engine.events[-2:] == ["ROLLBACK", "close"]
    assert "COMMIT" not in engine.events
    assert [r for r in caplog.records if r.name == LOGGER_NAME] == []


@pytest.mark.parametrize(
    "error,reason",
    [
        (
            psycopg.errors.UndefinedTable(
                'relation "transactions" does not exist'
            ),
            'relation "transactions" does not exist',
        ),
        (
            psycopg.errors.InsufficientPrivilege(
                "permission denied for table transactions"
            ),
            "permission denied for table transactions",
        ),
    ],
)
def test_missing_table_or_grant_logs_warning_and_means_unavailable(
    error, reason, caplog
):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    driver_error = wrapped(error, SELECT)
    engine = FakeEngine(query_error=driver_error)

    with pytest.raises(DatabaseUnavailableError) as exc_info:
        read_all(engine, SELECT, PARAMS, "transactions")

    assert exc_info.value.__cause__ is driver_error
    assert "ROLLBACK" in engine.events
    records = [r for r in caplog.records if r.name == LOGGER_NAME]
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (logging.WARNING, f"Não foi possível ler transactions: {reason}")
    ]


@pytest.mark.parametrize(
    "error",
    [
        psycopg.errors.UndefinedTable("relation does not exist"),
        psycopg.errors.QueryCanceled("canceling statement"),
    ],
)
def test_parameter_values_never_reach_the_log(error, caplog):
    caplog.set_level(logging.DEBUG)
    params = {"user_id": 987654, "years": [2025, 2026]}
    # SQLAlchemy's error text carries the parameters; only the driver
    # reason may be logged.
    driver_error = wrapped(error, SELECT, params)
    assert "987654" in str(driver_error)
    engine = FakeEngine(query_error=driver_error)

    with pytest.raises(DatabaseUnavailableError):
        read_all(engine, SELECT, params, "transactions")

    assert "987654" not in caplog.text
    assert "2025" not in caplog.text
    assert "2026" not in caplog.text
