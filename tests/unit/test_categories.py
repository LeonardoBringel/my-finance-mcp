"""Resolution of the category names a tool receives."""

from dataclasses import dataclass

import pytest

from my_finance_mcp.domain.categories import (
    CategorySelection,
    resolve_categories,
)
from my_finance_mcp.domain.errors import ToolInputError


@dataclass(frozen=True)
class Category:
    id: int
    name: str


MERCADO = Category(1, "Mercado")
SAUDE = Category(2, "Saúde")
USER_CATEGORIES = [MERCADO, SAUDE]


def _unknown(names, categories=USER_CATEGORIES) -> ToolInputError:
    with pytest.raises(ToolInputError) as exc_info:
        resolve_categories(names, categories)
    assert exc_info.value.code == "UNKNOWN_CATEGORY"
    return exc_info.value


def test_names_match_ignoring_case_accents_and_spaces_once_each():
    selection = resolve_categories(["SAUDE", "saúde "], USER_CATEGORIES)

    assert selection == CategorySelection(frozenset({2}), False)


def test_uncategorized_label_selects_transactions_without_category():
    selection = resolve_categories(["(Sem Categoria)"], USER_CATEGORIES)

    assert selection == CategorySelection(frozenset(), True)


def test_uncategorized_label_wins_over_a_user_category_with_that_name():
    categories = [MERCADO, Category(9, "(sem categoria)")]

    selection = resolve_categories(["(sem categoria)"], categories)

    assert selection == CategorySelection(frozenset(), True)


def test_names_and_label_combine_in_one_selection():
    selection = resolve_categories(
        ["mercado", "(sem categoria)"], USER_CATEGORIES
    )

    assert selection == CategorySelection(frozenset({1}), True)


@pytest.mark.parametrize("names", [None, []])
def test_no_names_means_no_category_filter(names):
    assert resolve_categories(names, USER_CATEGORIES) is None


def test_unknown_name_suggests_similar_user_categories():
    error = _unknown(["merkado"])

    assert error.text == (
        "UNKNOWN_CATEGORY: Categoria 'merkado' não encontrada. "
        "Categorias parecidas: Mercado."
    )


def test_error_cites_the_first_unknown_name_in_order():
    error = _unknown(["Mercado", "xpto", "abc"])

    assert error.message == "Categoria 'xpto' não encontrada."


def test_unknown_name_without_similar_category_has_no_hint():
    error = _unknown(["xpto"])

    assert error.hint == ""
    assert error.text == "UNKNOWN_CATEGORY: Categoria 'xpto' não encontrada."


def test_name_exactly_at_the_similarity_cutoff_is_suggested():
    # "mer" against "mercado" scores exactly 0.6 (2 * 3 / 10).
    error = _unknown(["mer"])

    assert error.hint == "Categorias parecidas: Mercado."


def test_name_just_below_the_similarity_cutoff_is_not_suggested():
    # "mercazzzzz" against "mercado" scores 10 / 17, about 0.588.
    error = _unknown(["mercazzzzz"])

    assert error.hint == ""


def test_suggestions_are_at_most_three_user_categories():
    categories = [Category(i, f"Lazer {i}") for i in range(1, 5)]

    error = _unknown(["lazer"], categories)

    assert error.hint == "Categorias parecidas: Lazer 4, Lazer 3, Lazer 2."


def test_suggestions_stay_at_three_when_one_match_groups_more_names():
    # Every name below normalizes to "lazer", so a single close match
    # carries five original names.
    names = ["Lazer", "Lázer", "Lazér", "Lâzer", "Lãzer"]
    categories = [Category(i, name) for i, name in enumerate(names, start=1)]

    error = _unknown(["lazerr"], categories)

    assert error.hint == "Categorias parecidas: Lazer, Lázer, Lazér."


def test_suggestions_never_offer_the_uncategorized_label():
    error = _unknown(["(sem categoriaa)"])

    assert error.hint == ""


def test_names_differing_only_by_accent_resolve_to_both_categories():
    categories = [Category(3, "Saude"), SAUDE]

    assert resolve_categories(["saude"], categories) == CategorySelection(
        frozenset({2, 3}), False
    )


def test_suggestions_list_every_original_name_of_a_match():
    categories = [Category(3, "Saude"), SAUDE]

    error = _unknown(["saudee"], categories)

    assert error.hint == "Categorias parecidas: Saude, Saúde."


@pytest.mark.parametrize(
    ("selection", "category_id", "expected"),
    [
        (CategorySelection(frozenset({2}), False), 2, True),
        (CategorySelection(frozenset({2}), False), 1, False),
        (CategorySelection(frozenset({2}), False), None, False),
        (CategorySelection(frozenset({2}), True), None, True),
        (CategorySelection(frozenset(), True), 2, False),
    ],
)
def test_selection_includes_selected_ids_and_optionally_uncategorized(
    selection, category_id, expected
):
    assert selection.includes(category_id) is expected
