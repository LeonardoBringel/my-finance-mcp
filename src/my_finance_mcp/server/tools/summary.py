"""The get_period_summary tool: dashboard totals of a period."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from my_finance_mcp.domain.money import pct, to_output
from my_finance_mcp.domain.periods import period_warnings, validate_period
from my_finance_mcp.server.runtime import CallContext, ToolOutput, ToolRuntime
from my_finance_mcp.server.schemas.envelope import (
    Period,
    ToolResult,
    WarningCode,
    ordered_warnings,
)
from my_finance_mcp.server.schemas.summary import (
    PeriodSummaryData,
    TransactionCount,
)
from my_finance_mcp.server.tools._types import EndDate, StartDate
from my_finance_mcp.services.summary import summarize_period


@dataclass(frozen=True)
class PeriodArgs:
    start_date: date
    end_date: date


def summary_handler(
    ctx: CallContext, args: PeriodArgs
) -> ToolOutput[PeriodSummaryData]:
    start, end = args.start_date, args.end_date
    validate_period(start, end)
    loaded = ctx.data.transactions_between(start, end)
    summary = summarize_period(loaded.rows, start, end, ctx.today)
    no_data: tuple[WarningCode, ...] = () if summary.has_data else ("NO_DATA",)
    data = PeriodSummaryData(
        entradas=to_output(summary.entradas),
        saidas=to_output(summary.saidas),
        investimentos=to_output(summary.investimentos),
        saldo=to_output(summary.saldo),
        transaction_count=TransactionCount(**summary.counts),
        avg_daily_expense=to_output(summary.avg_daily_expense),
        days_considered=summary.days_considered,
        inherited_installments_pct=pct(
            summary.inherited_saidas, summary.saidas
        ),
    )
    warnings = ordered_warnings(
        [*no_data, *period_warnings(start, end, ctx.today), *loaded.warnings]
    )
    return ToolOutput(
        data=data,
        warnings=warnings,
        period=Period(start_date=start, end_date=end),
    )


class SummaryTools:
    def register(self, finance_tool: Callable, runtime: ToolRuntime) -> None:
        @finance_tool
        def get_period_summary(
            start_date: StartDate, end_date: EndDate
        ) -> ToolResult[PeriodSummaryData]:
            """Resume entradas, saídas, investimentos e saldo de um período.
            Use para perguntas gerais sobre um período ("como foi meu mês?",
            "quanto gastei no ano?"). Investimentos NÃO entram em saídas nem
            no saldo: são um total à parte. Se o período inclui o mês
            corrente, ele está incompleto (warning PARTIAL_PERIOD) e a média
            diária é a medida justa para comparar.
            """
            return runtime.run(
                "get_period_summary",
                PeriodArgs(start_date, end_date),
                summary_handler,
                ToolResult[PeriodSummaryData],
            )
