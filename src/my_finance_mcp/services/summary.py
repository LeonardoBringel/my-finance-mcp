"""Dashboard totals of a period: income, expenses, investments, balance."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from my_finance_mcp.data.rows import TransactionRow
from my_finance_mcp.domain.category_types import Flow
from my_finance_mcp.domain.periods import days_considered


@dataclass(frozen=True)
class PeriodSummary:
    entradas: Decimal
    saidas: Decimal
    investimentos: Decimal
    counts: Mapping[Flow, int]
    days_considered: int
    # Expenses from installments after the first, bought in earlier months.
    inherited_saidas: Decimal

    @property
    def saldo(self) -> Decimal:
        # Investments are a separate total and never reduce the balance.
        return self.entradas - self.saidas

    @property
    def avg_daily_expense(self) -> Decimal:
        return self.saidas / self.days_considered

    @property
    def has_data(self) -> bool:
        return sum(self.counts.values()) > 0


def summarize_period(
    rows: Iterable[TransactionRow], start: date, end: date, today: date
) -> PeriodSummary:
    """Totals of rows already limited to the period.

    Rows without a known flow (an unknown category type) stay out of every
    total and count.
    """
    totals: dict[Flow, Decimal] = {
        "entrada": Decimal(0),
        "saida": Decimal(0),
        "investimento": Decimal(0),
    }
    counts: dict[Flow, int] = {"entrada": 0, "saida": 0, "investimento": 0}
    inherited = Decimal(0)
    for row in rows:
        if row.flow is None:
            continue
        totals[row.flow] += row.value
        counts[row.flow] += 1
        if row.flow == "saida" and (row.installment_number or 0) > 1:
            inherited += row.value
    return PeriodSummary(
        entradas=totals["entrada"],
        saidas=totals["saida"],
        investimentos=totals["investimento"],
        counts=counts,
        days_considered=days_considered(start, end, today),
        inherited_saidas=inherited,
    )
