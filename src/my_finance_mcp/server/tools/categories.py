"""The list_categories tool."""

from dataclasses import dataclass

from my_finance_mcp.domain.category_types import CategoryType
from my_finance_mcp.server.runtime import CallContext, ToolOutput, ToolRuntime
from my_finance_mcp.server.schemas.categories import (
    CategoriesData,
    CategoryItem,
)
from my_finance_mcp.server.schemas.envelope import ToolResult
from my_finance_mcp.server.tools._types import CategoryTypeFilter
from my_finance_mcp.services import categories as categories_service

TOOL_NAME = "list_categories"


@dataclass(frozen=True)
class CategoriesArgs:
    type: CategoryType | None


def categories_handler(
    ctx: CallContext, args: CategoriesArgs
) -> ToolOutput[CategoriesData]:
    listing = categories_service.list_categories(
        ctx.data.categories(), ctx.data.category_counts(), args.type
    )
    data = CategoriesData(
        categories=[
            CategoryItem(
                name=entry.name,
                type=entry.type,
                flow=entry.flow,
                transaction_count=entry.transaction_count,
            )
            for entry in listing.entries
        ],
        total_count=listing.total_count,
        uncategorized_transaction_count=listing.uncategorized,
    )
    returned = sum(entry.transaction_count for entry in listing.entries)
    returned += listing.uncategorized or 0
    return ToolOutput(
        data=data,
        warnings=("NO_DATA",) if returned == 0 else (),
        truncated=listing.truncated,
    )


class CategoryTools:
    def register(self, finance_tool, runtime: ToolRuntime) -> None:
        @finance_tool
        def list_categories(
            type: CategoryTypeFilter = None,
        ) -> ToolResult[CategoriesData]:
            """Lista as categorias do usuário com o tipo e a quantidade de
            transações. Use para descobrir os nomes exatos das categorias
            antes de filtrar outras tools, ou quando o usuário perguntar quais
            categorias tem. Não é preciso chamar antes de toda análise: as
            outras tools aceitam o nome sem diferenciar maiúsculas nem acentos
            e sugerem nomes parecidos quando não encontram."""
            return runtime.run(
                TOOL_NAME,
                CategoriesArgs(type),
                categories_handler,
                ToolResult[CategoriesData],
            )
