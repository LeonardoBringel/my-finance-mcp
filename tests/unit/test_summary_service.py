"""Dashboard totals of a period computed over decrypted rows."""

from datetime import date
from decimal import Decimal

import pytest

from my_finance_mcp.data.rows import TransactionRow
from my_finance_mcp.services.summary import summarize_period

TODAY = date(2026, 10, 8)
SEPTEMBER = (date(2026, 9, 1), date(2026, 9, 30))


def tx(
    value: str,
    flow: str | None,
    category_name: str = "Mercado",
    category_type: str | None = "saida",
    installment: tuple[int, int] | None = None,
) -> TransactionRow:
    number, total = installment if installment else (None, None)
    return TransactionRow(
        id=1,
        year=2026,
        date=date(2026, 9, 10),
        value=Decimal(value),
        description=None,
        category_id=1,
        category_name=category_name,
        category_type=category_type,
        flow=flow,
        installment_group="g1" if installment else None,
        installment_number=number,
        installment_total=total,
    )


def summarize(rows, period=SEPTEMBER, today=TODAY):
    return summarize_period(rows, period[0], period[1], today)


def test_totals_split_by_flow_and_leave_unknown_types_out():
    rows = [
        tx("5000.00", "entrada", "Salário", "entrada"),
        tx("100.10", "saida", "Mercado", "saida"),
        # A "both" category counts as an expense.
        tx("50.00", "saida", "Lazer", "ambos"),
        tx("20.00", "saida", "(sem categoria)", None),
        tx("300.00", "investimento", "Tesouro", "investimento"),
        tx("999.00", None, "Estranha", "xpto", installment=(2, 3)),
    ]

    summary = summarize(rows)

    assert summary.entradas == Decimal("5000.00")
    assert summary.saidas == Decimal("170.10")
    assert summary.investimentos == Decimal("300.00")
    assert summary.saldo == Decimal("4829.90")
    assert dict(summary.counts) == {
        "entrada": 1,
        "saida": 3,
        "investimento": 1,
    }
    assert summary.inherited_saidas == Decimal("0")
    assert summary.has_data is True


def test_balance_is_negative_when_expenses_exceed_income():
    rows = [
        tx("100.00", "entrada", "Salário", "entrada"),
        tx("250.00", "saida"),
    ]

    assert summarize(rows).saldo == Decimal("-150.00")


@pytest.mark.parametrize(
    ("period", "days"),
    [
        # Closed month in the past.
        (SEPTEMBER, 30),
        # Current month: only the days up to today.
        ((date(2026, 10, 1), date(2026, 10, 31)), 8),
        # Entirely in the future: the whole month.
        ((date(2026, 11, 1), date(2026, 11, 30)), 30),
        # A single day equal to today.
        ((TODAY, TODAY), 1),
    ],
)
def test_days_considered_around_today(period, days):
    assert summarize([tx("10.00", "saida")], period).days_considered == days


def test_average_daily_expense_divides_expenses_by_days_considered():
    rows = [tx("100.10", "saida"), tx("70.00", "saida")]

    september = summarize(rows)
    october = summarize(rows, (date(2026, 10, 1), date(2026, 10, 31)))

    assert september.avg_daily_expense == Decimal("5.67")
    assert october.avg_daily_expense == Decimal("21.2625")
    assert isinstance(october.avg_daily_expense, Decimal)


def test_average_daily_expense_is_zero_without_expenses():
    summary = summarize([tx("5000.00", "entrada", "Salário", "entrada")])

    assert summary.saidas == Decimal("0")
    assert summary.avg_daily_expense == Decimal("0")


def test_inherited_expenses_sum_only_later_installments_of_expenses():
    rows = [
        tx("50.00", "saida", installment=(2, 3)),
        # The first installment was bought in this period.
        tx("30.00", "saida", installment=(1, 3)),
        tx("120.00", "saida"),
        tx("400.00", "entrada", "Salário", "entrada", installment=(2, 3)),
    ]

    summary = summarize(rows)

    assert summary.saidas == Decimal("200.00")
    assert summary.inherited_saidas == Decimal("50.00")


def test_period_with_only_unknown_types_has_no_data_and_zero_totals():
    rows = [tx("999.00", None, "Estranha", "xpto")]

    summary = summarize(rows)

    assert summary.has_data is False
    assert (
        summary.entradas,
        summary.saidas,
        summary.investimentos,
        summary.saldo,
        summary.avg_daily_expense,
        summary.inherited_saidas,
    ) == (Decimal("0"),) * 6
    assert dict(summary.counts) == {
        "entrada": 0,
        "saida": 0,
        "investimento": 0,
    }
