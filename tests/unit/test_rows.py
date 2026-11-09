"""Output types of the data layer."""

import dataclasses
from datetime import date
from decimal import Decimal

import pytest

from my_finance_mcp.data.rows import (
    CategoryCounts,
    CategoryRow,
    LoadedRows,
    TransactionRow,
)

TRANSACTION = TransactionRow(
    id=1,
    year=2026,
    date=date(2026, 1, 5),
    value=Decimal("33.33"),
    description="Feira",
    category_id=1,
    category_name="Mercado",
    category_type="saida",
    flow="saida",
    installment_group=None,
    installment_number=None,
    installment_total=None,
)


def test_loaded_rows_without_skipped_rows_has_no_warning():
    assert LoadedRows(rows=(), skipped=0).warnings == ()


@pytest.mark.parametrize("skipped", [1, 2])
def test_loaded_rows_with_skipped_rows_warns(skipped):
    assert LoadedRows(rows=(), skipped=skipped).warnings == (
        "SKIPPED_INVALID_ROWS",
    )


@pytest.mark.parametrize(
    ("instance", "field"),
    [
        (CategoryRow(id=1, name="Mercado", type="saida", flow="saida"), "name"),
        (TRANSACTION, "value"),
        (LoadedRows(rows=(TRANSACTION,), skipped=0), "skipped"),
        (
            CategoryCounts(by_category={1: 214}, uncategorized=3),
            "uncategorized",
        ),
    ],
)
def test_rows_are_immutable(instance, field):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field, None)
