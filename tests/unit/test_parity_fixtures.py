"""The parity dataset covers the dashboard rules, and the golden every month.

The golden is what the my-finance dashboard returned for the dataset, so a
case missing from the dataset is a rule the parity tests never compare.
"""

import json
import math
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

PARITY = Path(__file__).resolve().parents[1] / "fixtures" / "parity"
KNOWN_TYPES = {"entrada", "saida", "ambos", "investimento"}
COMPARED_SUMMARY_FIELDS = {
    "entradas",
    "saidas",
    "investimentos",
    "saldo",
    "avg_daily_expense",
}
TRANSACTION_KEYS = {
    "id",
    "category_id",
    "date",
    "year",
    "value",
    "description",
    "installment_group",
    "installment_number",
    "installment_total",
    "plaintext",
}


def load(name: str) -> dict:
    return json.loads((PARITY / name).read_text(encoding="utf-8"))


DATASET = load("dataset.json")
GOLDEN = load("golden.json")
TRANSACTIONS = DATASET["transactions"]
CATEGORY_TYPES = {c["id"]: c["type"] for c in DATASET["categories"]}


def readable_date(text: str) -> date | None:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def month_of(row: dict) -> str | None:
    when = readable_date(row["date"])
    return None if when is None else f"{when.year}-{when.month:02d}"


def months_between(first: str, last: str) -> list[str]:
    year, month = map(int, first.split("-"))
    months = []
    while f"{year}-{month:02d}" <= last:
        months.append(f"{year}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


DATASET_MONTHS = sorted({m for m in map(month_of, TRANSACTIONS) if m})


def test_dataset_has_one_user_and_the_reference_date():
    assert type(DATASET["user_id"]) is int
    assert DATASET["reference_date"] == "2026-10-08"
    assert all(set(row) == TRANSACTION_KEYS for row in TRANSACTIONS)


def test_dataset_rows_cover_every_month_of_2025_and_2026():
    assert DATASET_MONTHS == months_between("2025-01", "2026-12")


def test_readable_rows_are_filed_under_the_year_of_their_date():
    for row in TRANSACTIONS:
        when = readable_date(row["date"])
        if when is not None:
            assert row["year"] == when.year, row


def test_transactions_use_the_four_category_types():
    used = {
        CATEGORY_TYPES[row["category_id"]]
        for row in TRANSACTIONS
        if row["category_id"] is not None
    }

    assert KNOWN_TYPES <= used


def test_dataset_has_an_uncategorized_transaction():
    assert any(row["category_id"] is None for row in TRANSACTIONS)


def test_dataset_has_a_transaction_of_an_unknown_category_type():
    unknown = {
        category_id
        for category_id, category_type in CATEGORY_TYPES.items()
        if category_type not in KNOWN_TYPES
    }

    assert any(row["category_id"] in unknown for row in TRANSACTIONS)


def test_an_installment_purchase_has_its_first_and_inherited_installments():
    numbers_by_group: dict[str, set[int]] = {}
    for row in TRANSACTIONS:
        if row["installment_group"] is not None:
            numbers_by_group.setdefault(row["installment_group"], set()).add(
                row["installment_number"]
            )

    assert any(
        1 in numbers and any(n > 1 for n in numbers)
        for numbers in numbers_by_group.values()
    )


def test_installments_fall_in_november_and_december_2026():
    months = {
        month_of(row)
        for row in TRANSACTIONS
        if row["installment_group"] is not None
    }

    assert {"2026-11", "2026-12"} <= months


def test_a_plain_text_row_has_a_readable_date_and_a_value():
    plain = [row for row in TRANSACTIONS if row["plaintext"]]

    assert any(
        readable_date(row["date"]) is not None and Decimal(row["value"]) > 0
        for row in plain
    )


def test_a_row_with_an_unreadable_date_is_filed_under_a_year():
    unreadable = [
        row for row in TRANSACTIONS if readable_date(row["date"]) is None
    ]

    assert unreadable
    assert all(row["year"] in (2025, 2026) for row in unreadable)


def test_every_value_has_exactly_two_decimal_places():
    for row in TRANSACTIONS:
        assert Decimal(row["value"]).as_tuple().exponent == -2, row


def test_golden_has_every_dataset_month_with_the_dashboard_parts():
    assert sorted(GOLDEN["months"]) == DATASET_MONTHS
    assert "2026-10" in GOLDEN["months"]
    for month in GOLDEN["months"].values():
        assert set(month) == {"summary", "expenses_by_cat", "income_by_cat"}
        assert COMPARED_SUMMARY_FIELDS <= set(month["summary"])


def test_golden_daily_average_counts_days_up_to_the_reference_date():
    # 2026-10-08: eight elapsed days in October, all of November ahead.
    for month, days in (("2026-10", 8), ("2026-11", 30)):
        summary = GOLDEN["months"][month]["summary"]

        assert summary["saidas"] > 0
        assert math.isclose(
            summary["avg_daily_expense"] * days, summary["saidas"]
        )


def test_golden_records_the_reference_date_and_the_my_finance_commit():
    assert GOLDEN["reference_date"] == DATASET["reference_date"]
    assert re.fullmatch(r"[0-9a-f]{40}", GOLDEN["my_finance_commit"])


def test_golden_has_the_annual_series_of_2025_and_2026():
    assert sorted(GOLDEN["annual"]) == ["2025", "2026"]
    for points in GOLDEN["annual"].values():
        assert [p["month"] for p in points] == [
            f"{m:02d}" for m in range(1, 13)
        ]
