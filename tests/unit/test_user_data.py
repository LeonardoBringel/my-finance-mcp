"""Per-call data facade: period loads, year cache and pass-through reads."""

import gc
import weakref
from datetime import date
from decimal import Decimal

import pytest

from my_finance_mcp.data import user_data
from my_finance_mcp.data.repositories import (
    categories_repository,
    transactions_repository,
)
from my_finance_mcp.data.rows import (
    CategoryCounts,
    CategoryRow,
    LoadedRows,
    TransactionRow,
)
from my_finance_mcp.data.user_data import DbUserData
from my_finance_mcp.server.runtime import CallCache

ENGINE = object()
FERNET = object()
USER_ID = 42


def tx(id: int, year: int, when: date) -> TransactionRow:
    return TransactionRow(
        id=id,
        year=year,
        date=when,
        value=Decimal("10"),
        description=None,
        category_id=None,
        category_name="(sem categoria)",
        category_type=None,
        flow="saida",
        installment_group=None,
        installment_number=None,
        installment_total=None,
    )


class FakeTransactions:
    """Stands in for list_by_years, serving rows from a per-year table."""

    def __init__(self, by_year: dict[int, LoadedRows] | None = None) -> None:
        self.by_year = by_year or {}
        self.calls: list[tuple] = []

    def __call__(self, engine, fernet, user_id, years):
        self.calls.append((engine, fernet, user_id, list(years)))
        return {
            year: self.by_year.get(year, LoadedRows(rows=(), skipped=0))
            for year in years
        }


@pytest.fixture
def fake_years(monkeypatch):
    fake = FakeTransactions()
    monkeypatch.setattr(transactions_repository, "list_by_years", fake)
    return fake


def make_data(cache=None, user_id=USER_ID) -> DbUserData:
    return DbUserData(
        ENGINE, FERNET, user_id, CallCache() if cache is None else cache
    )


def test_overlapping_periods_read_each_year_once(fake_years):
    data = make_data()

    data.transactions_between(date(2025, 12, 1), date(2026, 1, 31))
    data.transactions_between(date(2026, 1, 1), date(2026, 1, 31))

    assert fake_years.calls == [(ENGINE, FERNET, USER_ID, [2025, 2026])]


def test_only_missing_years_are_read(fake_years):
    data = make_data()

    data.transactions_between(date(2025, 12, 1), date(2026, 1, 31))
    data.transactions_between(date(2026, 1, 1), date(2026, 1, 31))
    data.transactions_between(date(2024, 6, 1), date(2026, 1, 31))

    assert [call[3] for call in fake_years.calls] == [[2025, 2026], [2024]]


def test_only_rows_inside_the_period_are_returned(fake_years):
    fake_years.by_year = {
        2025: LoadedRows(
            rows=(
                tx(1, 2025, date(2025, 11, 30)),
                tx(2, 2025, date(2025, 12, 1)),
            ),
            skipped=0,
        ),
        2026: LoadedRows(
            rows=(
                # Filed under 2026 although the date is in 2025.
                tx(3, 2026, date(2025, 12, 31)),
                tx(4, 2026, date(2026, 1, 1)),
                tx(5, 2026, date(2026, 1, 31)),
                tx(6, 2026, date(2026, 2, 1)),
            ),
            skipped=0,
        ),
    }
    data = make_data()

    wide = data.transactions_between(date(2025, 12, 1), date(2026, 1, 31))
    january = data.transactions_between(date(2026, 1, 1), date(2026, 1, 31))

    assert [row.id for row in wide.rows] == [2, 3, 4, 5]
    assert [row.id for row in january.rows] == [4, 5]


def test_skipped_is_the_sum_of_the_touched_years(fake_years):
    fake_years.by_year = {
        2024: LoadedRows(rows=(), skipped=5),
        2025: LoadedRows(rows=(), skipped=2),
        2026: LoadedRows(rows=(), skipped=1),
    }
    data = make_data()

    loaded = data.transactions_between(date(2025, 12, 1), date(2026, 1, 31))

    assert loaded.skipped == 3
    assert loaded.warnings == ("SKIPPED_INVALID_ROWS",)


def test_no_skipped_rows_means_no_warning(fake_years):
    fake_years.by_year = {
        2026: LoadedRows(rows=(tx(1, 2026, date(2026, 1, 5)),), skipped=0)
    }
    data = make_data()

    loaded = data.transactions_between(date(2026, 1, 1), date(2026, 1, 31))

    assert loaded.skipped == 0
    assert loaded.warnings == ()


def test_cache_keys_are_prefixed_by_user_and_year(fake_years):
    year_2025 = LoadedRows(rows=(tx(1, 2025, date(2025, 12, 5)),), skipped=0)
    year_2026 = LoadedRows(rows=(), skipped=4)
    fake_years.by_year = {2025: year_2025, 2026: year_2026}
    cache = CallCache()
    data = make_data(cache)

    data.transactions_between(date(2025, 12, 1), date(2026, 1, 31))

    assert cache == {
        ("transactions", USER_ID, 2025): year_2025,
        ("transactions", USER_ID, 2026): year_2026,
    }


def test_instances_with_different_caches_share_no_rows(monkeypatch):
    calls = []

    def list_by_years(engine, fernet, user_id, years):
        calls.append((user_id, list(years)))
        return {
            year: LoadedRows(
                rows=(tx(user_id, year, date(year, 1, 5)),), skipped=0
            )
            for year in years
        }

    monkeypatch.setattr(transactions_repository, "list_by_years", list_by_years)
    period = (date(2026, 1, 1), date(2026, 1, 31))

    first = make_data(user_id=42).transactions_between(*period)
    second = make_data(user_id=7).transactions_between(*period)

    assert calls == [(42, [2026]), (7, [2026])]
    assert [row.id for row in first.rows] == [42]
    assert [row.id for row in second.rows] == [7]


def test_rows_die_with_the_instance_and_its_cache(fake_years):
    fake_years.by_year = {
        2026: LoadedRows(rows=(tx(1, 2026, date(2026, 1, 5)),), skipped=0)
    }
    cache = CallCache()
    data = make_data(cache)
    data.transactions_between(date(2026, 1, 1), date(2026, 1, 31))
    cache_ref = weakref.ref(cache)
    data_ref = weakref.ref(data)

    del data, cache
    gc.collect()

    assert data_ref() is None
    assert cache_ref() is None


def test_categories_pass_the_instance_scope(monkeypatch):
    calls = []
    categories = [CategoryRow(id=1, name="Mercado", type="saida", flow="saida")]

    def list_categories(engine, fernet, user_id):
        calls.append((engine, fernet, user_id))
        return categories

    monkeypatch.setattr(
        categories_repository, "list_categories", list_categories
    )

    assert make_data().categories() is categories
    assert calls == [(ENGINE, FERNET, USER_ID)]


def test_category_counts_pass_the_instance_scope(monkeypatch):
    calls = []
    counts = CategoryCounts(by_category={1: 214}, uncategorized=3)

    def count_transactions_by_category(engine, user_id):
        calls.append((engine, user_id))
        return counts

    monkeypatch.setattr(
        categories_repository,
        "count_transactions_by_category",
        count_transactions_by_category,
    )

    assert make_data().category_counts() is counts
    assert calls == [(ENGINE, USER_ID)]


def test_available_years_pass_the_instance_scope(monkeypatch):
    calls = []

    def list_available_years(engine, user_id):
        calls.append((engine, user_id))
        return [2024, 2026]

    monkeypatch.setattr(
        transactions_repository, "list_available_years", list_available_years
    )

    assert make_data().available_years() == [2024, 2026]
    assert calls == [(ENGINE, USER_ID)]


def test_installments_pass_the_instance_scope(monkeypatch):
    calls = []
    loaded = LoadedRows(rows=(tx(1, 2026, date(2026, 1, 5)),), skipped=1)

    def list_installments(engine, fernet, user_id):
        calls.append((engine, fernet, user_id))
        return loaded

    monkeypatch.setattr(
        transactions_repository, "list_installments", list_installments
    )

    assert make_data().installments() is loaded
    assert calls == [(ENGINE, FERNET, USER_ID)]


def test_module_keeps_no_rows_of_its_own():
    module_values = [
        value
        for name, value in vars(user_data).items()
        if not name.startswith("__")
    ]

    assert not any(
        isinstance(value, (dict, list, LoadedRows, CallCache))
        for value in module_values
    )
