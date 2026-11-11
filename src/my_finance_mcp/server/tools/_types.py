"""Argument types shared by the tools, with their published descriptions."""

from datetime import date
from typing import Annotated

from pydantic import Field

from my_finance_mcp.domain.category_types import CategoryType

StartDate = Annotated[date, Field(description="Início do período, inclusivo.")]
EndDate = Annotated[date, Field(description="Fim do período, inclusivo.")]
CategoryTypeFilter = Annotated[
    CategoryType | None,
    Field(
        description=(
            "Filtra pelo tipo bruto da categoria. Omitido, retorna todas."
        )
    ),
]
