"""Output schema of the period summary tool."""

from pydantic import BaseModel


class TransactionCount(BaseModel):
    entrada: int
    saida: int
    investimento: int


class PeriodSummaryData(BaseModel):
    entradas: float
    saidas: float
    investimentos: float
    saldo: float
    transaction_count: TransactionCount
    avg_daily_expense: float
    days_considered: int
    # None when the period has no expenses to take a share of.
    inherited_installments_pct: float | None
