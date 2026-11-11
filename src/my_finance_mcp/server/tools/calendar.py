"""The get_date_context tool: today's date and the relative periods."""

from my_finance_mcp.domain.clock import SAO_PAULO
from my_finance_mcp.server.runtime import CallContext, ToolOutput, ToolRuntime
from my_finance_mcp.server.schemas.date_context import (
    DateContextData,
    RelativePeriod,
    RelativePeriods,
)
from my_finance_mcp.server.schemas.envelope import ToolResult
from my_finance_mcp.services.date_context import WEEKDAYS, relative_periods

TOOL_NAME = "get_date_context"


def date_context_handler(
    ctx: CallContext, args: None
) -> ToolOutput[DateContextData]:
    years = sorted(ctx.data.available_years())
    periods = {
        key: RelativePeriod(
            start_date=period.start, end_date=period.end, partial=period.partial
        )
        for key, period in relative_periods(ctx.today).items()
    }
    data = DateContextData(
        today=ctx.today,
        weekday=WEEKDAYS[ctx.today.weekday()],
        timezone=SAO_PAULO.key,
        periods=RelativePeriods(**periods),
        available_years=years,
    )
    # A user with no year on record has no transaction at all.
    warnings = () if years else ("NO_DATA",)
    return ToolOutput(data=data, warnings=warnings)


class CalendarTools:
    def register(self, finance_tool, runtime: ToolRuntime) -> None:
        @finance_tool
        def get_date_context() -> ToolResult[DateContextData]:
            """Devolve a data de hoje do usuário (fuso America/Sao_Paulo) e os
            limites dos períodos relativos usados pelas outras tools. Chame
            antes de interpretar expressões como "este mês", "mês passado",
            "esta semana", "últimos 3 meses" ou um nome de mês sem ano, se
            você não souber a data de hoje no fuso do usuário. Não recebe
            argumentos."""
            return runtime.run(
                TOOL_NAME,
                None,
                date_context_handler,
                ToolResult[DateContextData],
            )
