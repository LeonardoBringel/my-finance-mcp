"""Decrypted reads of the user's transactions."""

import logging
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from cryptography.fernet import Fernet

from my_finance_mcp.data.db import DatabaseUnavailableError
from my_finance_mcp.data.repositories.transactions_repository import (
    list_available_years,
    list_by_years,
    list_installments,
)
from my_finance_mcp.data.rows import LoadedRows, TransactionRow
from tests.unit.db_fakes import SET_TIMEOUT, FakeEngine, wrapped

FERNET = Fernet(Fernet.generate_key())
USER_ID = 42

SELECT_BY_YEARS = (
    "SELECT t.id, t.year, t.date, t.value, t.description, c.id, c.name, "
    "c.type, t.installment_group, t.installment_number, t.installment_total "
    "FROM transactions t LEFT JOIN categories c ON c.id = t.category_id "
    "AND c.user_id = t.user_id WHERE t.user_id = :user_id "
    "AND t.year = ANY(:years)"
)
SELECT_INSTALLMENTS = (
    "SELECT t.id, t.year, t.date, t.value, t.description, c.id, c.name, "
    "c.type, t.installment_group, t.installment_number, t.installment_total "
    "FROM transactions t LEFT JOIN categories c ON c.id = t.category_id "
    "AND c.user_id = t.user_id WHERE t.user_id = :user_id "
    "AND t.installment_group IS NOT NULL"
)
SELECT_AVAILABLE_YEARS = (
    "SELECT DISTINCT year FROM transactions WHERE user_id = :user_id "
    "AND year IS NOT NULL ORDER BY year"
)


def enc(text: str) -> str:
    return FERNET.encrypt(text.encode("utf-8")).decode("ascii")


def db_row(
    id=1,
    year=2026,
    date_text="2026-01-10",
    value="10.00",
    description="Padaria",
    category=(7, "Mercado", "saida"),
    installment=(None, None, None),
):
    """A transactions row as the driver returns it, encrypted columns included."""
    category_id, name, category_type = category
    return (
        id,
        year,
        None if date_text is None else enc(date_text),
        enc(value),
        None if description is None else enc(description),
        category_id,
        None if name is None else enc(name),
        None if category_type is None else enc(category_type),
        *installment,
    )


def test_list_by_years_reads_every_year_in_one_bounded_query():
    engine = FakeEngine()

    list_by_years(engine, FERNET, USER_ID, [2025, 2026])

    assert engine.events == [
        "connect",
        "BEGIN",
        (SET_TIMEOUT, None),
        (SELECT_BY_YEARS, {"user_id": 42, "years": [2025, 2026]}),
        "COMMIT",
        "close",
    ]


@pytest.mark.parametrize(
    "read",
    [
        lambda engine: list_by_years(engine, FERNET, USER_ID, [2025, 2026]),
        lambda engine: list_installments(engine, FERNET, USER_ID),
        lambda engine: list_available_years(engine, USER_ID),
    ],
    ids=["by_years", "installments", "available_years"],
)
def test_user_id_is_a_bind_parameter_never_in_the_sql_text(read):
    engine = FakeEngine()

    read(engine)

    [(sql, params)] = engine.queries()
    assert params["user_id"] == 42
    assert "42" not in sql


def test_every_requested_year_is_a_key_and_rows_follow_the_year_column():
    # The year column decides the bucket, even when the date says otherwise.
    late_entry = db_row(id=3, year=2026, date_text="2025-12-31")
    engine = FakeEngine(
        {SELECT_BY_YEARS: [db_row(id=1), late_entry, db_row(id=2)]}
    )

    result = list_by_years(engine, FERNET, USER_ID, [2025, 2026])

    assert list(result) == [2025, 2026]
    assert result[2025] == LoadedRows(rows=(), skipped=0)
    assert [row.id for row in result[2026].rows] == [1, 3, 2]
    assert result[2026].rows[1].date == date(2025, 12, 31)
    assert result[2026].skipped == 0


def test_encrypted_row_becomes_a_decrypted_transaction():
    engine = FakeEngine(
        {
            SELECT_BY_YEARS: [
                db_row(
                    id=11,
                    year=2026,
                    date_text="2026-03-15",
                    value="-33.33",
                    description="Salário março",
                    category=(5, "Salário", "entrada"),
                    installment=("grp-1", 2, 10),
                )
            ]
        }
    )

    [row] = list_by_years(engine, FERNET, USER_ID, [2026])[2026].rows

    assert row == TransactionRow(
        id=11,
        year=2026,
        date=date(2026, 3, 15),
        value=Decimal("-33.33"),
        description="Salário março",
        category_id=5,
        category_name="Salário",
        category_type="entrada",
        flow="entrada",
        installment_group="grp-1",
        installment_number=2,
        installment_total=10,
    )
    assert type(row.date) is date
    assert type(row.value) is Decimal


def test_unknown_category_type_leaves_the_flow_empty():
    engine = FakeEngine(
        {SELECT_BY_YEARS: [db_row(category=(5, "Diversos", "outro"))]}
    )

    [row] = list_by_years(engine, FERNET, USER_ID, [2026])[2026].rows

    assert (row.category_type, row.flow) == ("outro", None)


def test_row_without_category_is_uncategorized_expense():
    # The join also yields no category when it belongs to another user.
    engine = FakeEngine(
        {SELECT_BY_YEARS: [db_row(category=(None, None, None))]}
    )

    [row] = list_by_years(engine, FERNET, USER_ID, [2026])[2026].rows

    assert row.category_id is None
    assert row.category_name == "(sem categoria)"
    assert row.category_type is None
    assert row.flow == "saida"


def test_unreadable_dates_are_dropped_and_counted_per_year():
    engine = FakeEngine(
        {
            SELECT_BY_YEARS: [
                db_row(id=1, year=2025, date_text="31/12/2025"),
                db_row(id=2, year=2025, date_text="2026-02-30"),
                db_row(id=3, year=2025, date_text=None),
                db_row(id=4, year=2025, date_text="2025-06-01"),
                db_row(id=5, year=2026, date_text="2026-01-02"),
            ]
        }
    )

    result = list_by_years(engine, FERNET, USER_ID, [2025, 2026])

    assert [row.id for row in result[2025].rows] == [4]
    assert result[2025].skipped == 3
    assert [row.id for row in result[2026].rows] == [5]
    assert result[2026].skipped == 0


def test_single_digit_month_and_day_are_accepted():
    # my-finance parses dates the same way, so its dashboard counts them too.
    engine = FakeEngine({SELECT_BY_YEARS: [db_row(date_text="2026-1-5")]})

    result = list_by_years(engine, FERNET, USER_ID, [2026])[2026]

    assert [row.date for row in result.rows] == [date(2026, 1, 5)]
    assert result.skipped == 0


@pytest.mark.parametrize(
    "stored",
    [None, "", enc("")],
    ids=["null", "empty_plain", "empty_encrypted"],
)
def test_empty_description_becomes_none(stored):
    stored_row = list(db_row())
    stored_row[4] = stored
    engine = FakeEngine({SELECT_BY_YEARS: [tuple(stored_row)]})

    [row] = list_by_years(engine, FERNET, USER_ID, [2026])[2026].rows

    assert row.description is None


def test_list_installments_reads_grouped_rows_of_every_year():
    engine = FakeEngine(
        {
            SELECT_INSTALLMENTS: [
                db_row(id=1, year=2024, installment=("grp-a", 1, 3)),
                db_row(id=2, year=2026, date_text="2026-13-01"),
                db_row(
                    id=3,
                    year=2026,
                    category=(None, None, None),
                    installment=("grp-a", 3, 3),
                ),
            ]
        }
    )

    result = list_installments(engine, FERNET, USER_ID)

    assert engine.queries() == [(SELECT_INSTALLMENTS, {"user_id": 42})]
    assert [row.id for row in result.rows] == [1, 3]
    assert result.skipped == 1
    first, last = result.rows
    assert (
        first.installment_group,
        first.installment_number,
        first.installment_total,
    ) == ("grp-a", 1, 3)
    assert (last.category_name, last.flow) == ("(sem categoria)", "saida")


def test_list_available_years_returns_the_years_as_read():
    engine = FakeEngine({SELECT_AVAILABLE_YEARS: [(2024,), (2026,)]})

    years = list_available_years(engine, USER_ID)

    assert years == [2024, 2026]
    assert type(years) is list
    assert engine.queries() == [(SELECT_AVAILABLE_YEARS, {"user_id": 42})]


@pytest.mark.parametrize(
    "read",
    [
        lambda engine: list_by_years(engine, FERNET, USER_ID, [2026]),
        lambda engine: list_installments(engine, FERNET, USER_ID),
        lambda engine: list_available_years(engine, USER_ID),
    ],
    ids=["by_years", "installments", "available_years"],
)
def test_missing_grant_names_the_transactions_table(read, caplog):
    caplog.set_level(logging.WARNING)
    reason = "permission denied for table transactions"
    error = wrapped(psycopg.errors.InsufficientPrivilege(reason))
    engine = FakeEngine(query_error=error)

    with pytest.raises(DatabaseUnavailableError) as exc_info:
        read(engine)

    assert exc_info.value.__cause__ is error
    assert [r.getMessage() for r in caplog.records] == [
        f"Não foi possível ler transactions: {reason}"
    ]


def test_decrypted_values_never_reach_the_log(caplog):
    caplog.set_level(logging.DEBUG)
    rows = [
        db_row(
            id=1,
            date_text="2026-04-07",
            value="1234.56",
            description="Consulta Dra Helena",
            category=(9, "Saúde Familiar", "investimento"),
        ),
        db_row(id=2, date_text="07/04/2026", description="Farmácia Central"),
    ]
    engine = FakeEngine({SELECT_BY_YEARS: rows, SELECT_INSTALLMENTS: rows})

    list_by_years(engine, FERNET, USER_ID, [2026])
    list_installments(engine, FERNET, USER_ID)

    for secret in (
        "2026-04-07",
        "1234.56",
        "Consulta Dra Helena",
        "Saúde Familiar",
        "investimento",
        "07/04/2026",
        "Farmácia Central",
    ):
        assert secret not in caplog.text
