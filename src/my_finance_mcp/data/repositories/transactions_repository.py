"""Explicit SQL reads of the user's transactions, already decrypted."""

from collections.abc import Sequence
from datetime import datetime

from cryptography.fernet import Fernet
from sqlalchemy import Engine, Row

from my_finance_mcp.data.crypto import decrypt, decrypt_decimal
from my_finance_mcp.data.rows import LoadedRows, TransactionRow
from my_finance_mcp.data.sql import read_all
from my_finance_mcp.domain.category_types import UNCATEGORIZED_LABEL, flow_of

# Matching the user on the join turns a category of another user into no
# category, so its name never leaves this read.
_SELECT_ROWS = (
    "SELECT t.id, t.year, t.date, t.value, t.description, c.id, c.name, "
    "c.type, t.installment_group, t.installment_number, t.installment_total "
    "FROM transactions t LEFT JOIN categories c ON c.id = t.category_id "
    "AND c.user_id = t.user_id WHERE t.user_id = :user_id"
)
SELECT_BY_YEARS = _SELECT_ROWS + " AND t.year = ANY(:years)"
SELECT_INSTALLMENTS = _SELECT_ROWS + " AND t.installment_group IS NOT NULL"
SELECT_AVAILABLE_YEARS = (
    "SELECT DISTINCT year FROM transactions WHERE user_id = :user_id "
    "AND year IS NOT NULL ORDER BY year"
)

_TABLE = "transactions"


def list_by_years(
    engine: Engine, fernet: Fernet, user_id: int, years: Sequence[int]
) -> dict[int, LoadedRows]:
    """Rows of each requested year, every year present even when empty."""
    # psycopg adapts a list, not a tuple, to a PostgreSQL array.
    params = {"user_id": user_id, "years": list(years)}
    kept: dict[int, list[TransactionRow]] = {year: [] for year in years}
    skipped = dict.fromkeys(years, 0)
    for row in read_all(engine, SELECT_BY_YEARS, params, _TABLE):
        transaction = _to_transaction(row, fernet)
        if transaction is None:
            skipped[row[1]] += 1
        else:
            kept[row[1]].append(transaction)
    return {
        year: LoadedRows(rows=tuple(kept[year]), skipped=skipped[year])
        for year in years
    }


def list_installments(
    engine: Engine, fernet: Fernet, user_id: int
) -> LoadedRows:
    """Rows of every year that belong to an installment group."""
    rows = read_all(engine, SELECT_INSTALLMENTS, {"user_id": user_id}, _TABLE)
    transactions = [_to_transaction(row, fernet) for row in rows]
    kept = tuple(t for t in transactions if t is not None)
    return LoadedRows(rows=kept, skipped=len(transactions) - len(kept))


def list_available_years(engine: Engine, user_id: int) -> list[int]:
    """Distinct years with a transaction, ascending; nothing is decrypted."""
    rows = read_all(
        engine, SELECT_AVAILABLE_YEARS, {"user_id": user_id}, _TABLE
    )
    return [row[0] for row in rows]


def _to_transaction(row: Row, fernet: Fernet) -> TransactionRow | None:
    """The decrypted transaction, or None when its date cannot be read."""
    (
        id_,
        year,
        date_token,
        value_token,
        description_token,
        category_id,
        name_token,
        type_token,
        installment_group,
        installment_number,
        installment_total,
    ) = row
    try:
        # Same parsing as my-finance, which also accepts "2026-1-5".
        when = datetime.strptime(decrypt(date_token, fernet), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    if category_id is None:
        name, category_type, flow = UNCATEGORIZED_LABEL, None, "saida"
    else:
        name = decrypt(name_token, fernet) or ""
        category_type = decrypt(type_token, fernet)
        flow = flow_of(category_type)
    return TransactionRow(
        id=id_,
        year=year,
        date=when,
        value=decrypt_decimal(value_token, fernet),
        description=decrypt(description_token, fernet) or None,
        category_id=category_id,
        category_name=name,
        category_type=category_type,
        flow=flow,
        installment_group=installment_group,
        installment_number=installment_number,
        installment_total=installment_total,
    )
