"""Resolution of category names sent by the model to the user's categories."""

import difflib
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from my_finance_mcp.domain.category_types import UNCATEGORIZED_LABEL
from my_finance_mcp.domain.errors import ToolInputError

_MAX_SUGGESTIONS = 3
_SUGGESTION_CUTOFF = 0.6


class NamedCategory(Protocol):
    id: int
    name: str


@dataclass(frozen=True)
class CategorySelection:
    category_ids: frozenset[int]
    include_uncategorized: bool

    def includes(self, category_id: int | None) -> bool:
        if category_id is None:
            return self.include_uncategorized
        return category_id in self.category_ids


def normalize_name(name: str) -> str:
    """Comparable form of a name: no outer spaces, no case, no accents."""
    decomposed = unicodedata.normalize("NFKD", name.strip().casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


_UNCATEGORIZED_KEY = normalize_name(UNCATEGORIZED_LABEL)


def resolve_categories(
    names: Sequence[str] | None, categories: Sequence[NamedCategory]
) -> CategorySelection | None:
    """Selection for the given names; None means no category filter.

    Two categories that differ only by accents normalize to the same name,
    so that name selects both of them.
    """
    if not names:
        return None
    by_key: dict[str, list[NamedCategory]] = {}
    for category in categories:
        by_key.setdefault(normalize_name(category.name), []).append(category)

    ids: set[int] = set()
    include_uncategorized = False
    for name in names:
        key = normalize_name(name)
        # The label is reserved: it selects transactions without a category
        # even if the user named one of their categories like it.
        if key == _UNCATEGORIZED_KEY:
            include_uncategorized = True
        elif key in by_key:
            ids.update(category.id for category in by_key[key])
        else:
            raise _unknown_category(name, key, by_key)
    return CategorySelection(frozenset(ids), include_uncategorized)


def _unknown_category(
    name: str, key: str, by_key: dict[str, list[NamedCategory]]
) -> ToolInputError:
    matches = difflib.get_close_matches(
        key, by_key, n=_MAX_SUGGESTIONS, cutoff=_SUGGESTION_CUTOFF
    )
    suggestions = [c.name for match in matches for c in by_key[match]]
    hint = ""
    if suggestions:
        shown = ", ".join(suggestions[:_MAX_SUGGESTIONS])
        hint = f"Categorias parecidas: {shown}."
    return ToolInputError(
        "UNKNOWN_CATEGORY", f"Categoria '{name}' não encontrada.", hint
    )
