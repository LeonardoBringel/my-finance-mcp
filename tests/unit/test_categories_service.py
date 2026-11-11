"""Listing of the user's categories with their type, flow and counts."""

import pytest

from my_finance_mcp.data.rows import CategoryCounts, CategoryRow
from my_finance_mcp.domain.category_types import flow_of
from my_finance_mcp.services.categories import (
    CategoryEntry,
    CategoryListing,
    list_categories,
)


def category(id: int, name: str, type: str | None) -> CategoryRow:
    return CategoryRow(id=id, name=name, type=type, flow=flow_of(type))


SALARIO = category(1, "Salário", "entrada")
TESOURO = category(2, "Tesouro", "investimento")
MERCADO = category(3, "Mercado", "saida")
EXAMPLE_COUNTS = CategoryCounts(
    by_category={1: 21, 2: 9, 3: 214}, uncategorized=3
)

LAZER = category(10, "Lazer", "ambos")
MIXED = [
    MERCADO,
    SALARIO,
    TESOURO,
    LAZER,
    category(11, "Viagem", "xpto"),
    category(12, "Antigo", None),
]
MIXED_COUNTS = CategoryCounts(
    by_category={3: 5, 1: 2, 2: 1, 10: 4, 11: 7, 12: 6}, uncategorized=8
)


def names(listing: CategoryListing) -> list[str]:
    return [entry.name for entry in listing.entries]


def test_unfiltered_listing_matches_the_published_example():
    listing = list_categories([SALARIO, TESOURO, MERCADO], EXAMPLE_COUNTS, None)

    assert listing == CategoryListing(
        entries=(
            CategoryEntry("Mercado", "saida", "saida", 214),
            CategoryEntry("Salário", "entrada", "entrada", 21),
            CategoryEntry("Tesouro", "investimento", "investimento", 9),
        ),
        total_count=3,
        uncategorized=3,
        truncated=False,
    )


def test_names_sort_by_normalized_name_then_original_name_then_id():
    rows = [
        category(6, "Mercado", "saida"),
        category(5, "Lazer", "investimento"),
        category(2, "lazer", "saida"),
        category(3, "Lázer", "saida"),
        category(4, "Lazer", "entrada"),
        category(1, "Academia", "saida"),
    ]
    counts = CategoryCounts(by_category={}, uncategorized=0)

    listing = list_categories(rows, counts, None)

    # All four spellings normalize to "lazer", so they stay together before
    # "Mercado"; among them "Lazer" < "Lázer" < "lazer" by code point, and
    # the two "Lazer" follow their ids (4 is the "entrada" one).
    assert [(e.name, e.type) for e in listing.entries] == [
        ("Academia", "saida"),
        ("Lazer", "entrada"),
        ("Lazer", "investimento"),
        ("Lázer", "saida"),
        ("lazer", "saida"),
        ("Mercado", "saida"),
    ]


def test_ambos_filter_returns_only_ambos_categories_counted_as_expense():
    listing = list_categories(MIXED, MIXED_COUNTS, "ambos")

    assert listing.entries == (CategoryEntry("Lazer", "ambos", "saida", 4),)
    assert listing.total_count == 1


@pytest.mark.parametrize(
    ("type_filter", "expected"),
    [
        ("saida", [CategoryEntry("Mercado", "saida", "saida", 5)]),
        ("entrada", [CategoryEntry("Salário", "entrada", "entrada", 2)]),
        (
            "investimento",
            [CategoryEntry("Tesouro", "investimento", "investimento", 1)],
        ),
    ],
)
def test_type_filter_keeps_only_categories_of_that_raw_type(
    type_filter, expected
):
    listing = list_categories(MIXED, MIXED_COUNTS, type_filter)

    assert list(listing.entries) == expected


@pytest.mark.parametrize(
    ("type_filter", "uncategorized"),
    [
        (None, 8),
        ("saida", 8),
        ("entrada", None),
        ("investimento", None),
        ("ambos", None),
    ],
)
def test_uncategorized_count_only_without_filter_or_for_expenses(
    type_filter, uncategorized
):
    listing = list_categories(MIXED, MIXED_COUNTS, type_filter)

    assert listing.uncategorized == uncategorized


def test_category_without_transactions_has_zero_count():
    counts = CategoryCounts(by_category={1: 21}, uncategorized=0)

    listing = list_categories([SALARIO, MERCADO], counts, None)

    assert listing.entries == (
        CategoryEntry("Mercado", "saida", "saida", 0),
        CategoryEntry("Salário", "entrada", "entrada", 21),
    )


def many_categories(n: int) -> list[CategoryRow]:
    return [category(i, f"Cat {i:03d}", "saida") for i in range(n)]


def test_listing_over_the_limit_keeps_the_first_200_in_order():
    counts = CategoryCounts(by_category={}, uncategorized=0)

    listing = list_categories(many_categories(201), counts, None)

    assert len(listing.entries) == 200
    assert listing.entries[0].name == "Cat 000"
    assert listing.entries[-1].name == "Cat 199"
    assert listing.total_count == 201
    assert listing.truncated is True


def test_listing_at_the_limit_is_not_truncated():
    counts = CategoryCounts(by_category={}, uncategorized=0)

    listing = list_categories(many_categories(200), counts, None)

    assert len(listing.entries) == 200
    assert listing.total_count == 200
    assert listing.truncated is False


def test_unknown_types_appear_only_without_filter_with_no_type_or_flow():
    unfiltered = list_categories(MIXED, MIXED_COUNTS, None)

    assert names(unfiltered) == [
        "Antigo",
        "Lazer",
        "Mercado",
        "Salário",
        "Tesouro",
        "Viagem",
    ]
    assert unfiltered.entries[0] == CategoryEntry("Antigo", None, None, 6)
    assert unfiltered.entries[-1] == CategoryEntry("Viagem", None, None, 7)
    for type_filter in ("saida", "entrada", "investimento", "ambos"):
        filtered = list_categories(MIXED, MIXED_COUNTS, type_filter)
        assert "Antigo" not in names(filtered)
        assert "Viagem" not in names(filtered)


def test_empty_name_is_listed_with_the_unnamed_label():
    counts = CategoryCounts(by_category={8: 2}, uncategorized=0)

    listing = list_categories([category(8, "", "saida")], counts, None)

    assert listing.entries == (
        CategoryEntry("(sem nome)", "saida", "saida", 2),
    )
