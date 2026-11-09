"""Categories of the user and transaction counts per category."""

import logging

import psycopg
import pytest
from cryptography.fernet import Fernet

from my_finance_mcp.data.db import DatabaseUnavailableError
from my_finance_mcp.data.repositories.categories_repository import (
    count_transactions_by_category,
    list_categories,
)
from my_finance_mcp.data.rows import CategoryCounts, CategoryRow
from tests.unit.db_fakes import FakeEngine, wrapped

USER_ID = 42
SELECT_CATEGORIES = (
    "SELECT id, name, type FROM categories WHERE user_id = :user_id"
)
COUNT_BY_CATEGORY = (
    "SELECT c.id, COUNT(*) FROM transactions t LEFT JOIN categories c "
    "ON c.id = t.category_id AND c.user_id = t.user_id "
    "WHERE t.user_id = :user_id GROUP BY c.id"
)


@pytest.fixture
def fernet():
    return Fernet(Fernet.generate_key())


def encrypt(fernet: Fernet, text: str) -> str:
    return fernet.encrypt(text.encode("utf-8")).decode("ascii")


def test_list_categories_binds_user_id_and_never_puts_it_in_the_sql(fernet):
    engine = FakeEngine()

    assert list_categories(engine, fernet, USER_ID) == []

    assert engine.queries() == [(SELECT_CATEGORIES, {"user_id": 42})]
    assert "42" not in SELECT_CATEGORIES


def test_list_categories_decrypts_name_and_type_and_derives_flow(fernet):
    foreign_type = encrypt(Fernet(Fernet.generate_key()), "saida")
    engine = FakeEngine(
        {
            SELECT_CATEGORIES: [
                (1, encrypt(fernet, "Mercado"), encrypt(fernet, "saida")),
                (2, encrypt(fernet, "Salário"), encrypt(fernet, "entrada")),
                (
                    3,
                    encrypt(fernet, "Tesouro"),
                    encrypt(fernet, "investimento"),
                ),
                (4, encrypt(fernet, "Cartão"), encrypt(fernet, "ambos")),
                (5, encrypt(fernet, "Antiga"), foreign_type),
                (6, encrypt(fernet, ""), encrypt(fernet, "saida")),
            ]
        }
    )

    assert list_categories(engine, fernet, USER_ID) == [
        CategoryRow(id=1, name="Mercado", type="saida", flow="saida"),
        CategoryRow(id=2, name="Salário", type="entrada", flow="entrada"),
        CategoryRow(
            id=3, name="Tesouro", type="investimento", flow="investimento"
        ),
        CategoryRow(id=4, name="Cartão", type="ambos", flow="saida"),
        # A type that does not decrypt is kept raw and counts toward no flow.
        CategoryRow(id=5, name="Antiga", type=foreign_type, flow=None),
        CategoryRow(id=6, name="", type="saida", flow="saida"),
    ]


def test_count_binds_user_id_and_joins_categories_on_the_same_user():
    engine = FakeEngine()

    count_transactions_by_category(engine, USER_ID)

    assert engine.queries() == [(COUNT_BY_CATEGORY, {"user_id": 42})]
    assert "42" not in COUNT_BY_CATEGORY
    # Without the user in the join, a transaction pointing at another
    # user's category would count toward that category instead of the
    # uncategorized group.
    assert "ON c.id = t.category_id AND c.user_id = t.user_id" in (
        COUNT_BY_CATEGORY
    )


def test_count_splits_user_categories_from_the_uncategorized_group():
    engine = FakeEngine({COUNT_BY_CATEGORY: [(1, 214), (2, 9), (None, 3)]})

    assert count_transactions_by_category(engine, USER_ID) == CategoryCounts(
        by_category={1: 214, 2: 9}, uncategorized=3
    )


def test_count_without_uncategorized_group_is_zero():
    engine = FakeEngine({COUNT_BY_CATEGORY: [(1, 214), (2, 9)]})

    assert count_transactions_by_category(engine, USER_ID) == CategoryCounts(
        by_category={1: 214, 2: 9}, uncategorized=0
    )


def test_decrypted_names_and_types_never_reach_the_log(fernet, caplog):
    caplog.set_level(logging.DEBUG)
    engine = FakeEngine(
        {
            SELECT_CATEGORIES: [
                (
                    1,
                    encrypt(fernet, "Farmácia"),
                    encrypt(fernet, "investimento"),
                )
            ]
        }
    )

    list_categories(engine, fernet, USER_ID)

    assert "Farmácia" not in caplog.text
    assert "investimento" not in caplog.text


def test_query_error_logs_only_the_database_reason(fernet, caplog):
    caplog.set_level(logging.DEBUG)
    error = wrapped(
        psycopg.errors.InsufficientPrivilege(
            "permission denied for table categories"
        ),
        SELECT_CATEGORIES,
        {"user_id": USER_ID},
    )
    engine = FakeEngine(query_error=error)

    with pytest.raises(DatabaseUnavailableError):
        list_categories(engine, fernet, USER_ID)

    assert [r.getMessage() for r in caplog.records] == [
        "Não foi possível ler categories: "
        "permission denied for table categories"
    ]
