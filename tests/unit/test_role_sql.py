"""Static checks on the SQL that provisions the read-only database role."""

import re
from pathlib import Path

import pytest

SQL_PATH = Path(__file__).resolve().parents[2] / "deploy/db/create_role.sql"

ROLE = "my_finance_mcp"

EXPECTED_GRANTS = [
    f"GRANT USAGE ON SCHEMA public TO {ROLE};",
    f"GRANT SELECT ON transactions, categories, alembic_version TO {ROLE};",
    f"GRANT SELECT (id, username_hash, token_version) ON users TO {ROLE};",
]

FORBIDDEN_PATTERNS = [
    r"INSERT",
    r"UPDATE",
    r"DELETE",
    r"TRUNCATE",
    r"ALL\s+PRIVILEGES",
    r"cash_flow",
    r"password_hash",
    r"is_admin",
    r"\busername\b",
]


def read_sql() -> str:
    """SQL text without `--` comments, with whitespace runs collapsed."""
    lines = [
        re.sub(r"--.*$", "", line) for line in SQL_PATH.read_text().splitlines()
    ]
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def position(pattern: str, sql: str) -> int:
    match = re.search(pattern, sql)
    assert match, f"{pattern!r} not found in the role script"
    return match.start()


def test_guard_quits_before_any_statement_that_changes_the_database():
    sql = read_sql()

    guard = position(
        r"\\if :\{\?mcp_password\} \\else \\echo '[^']*' \\quit \\endif", sql
    )
    first_change = position(r"\b(CREATE|ALTER|REVOKE|GRANT)\b", sql)

    assert guard < first_change


def test_role_is_created_only_when_missing_from_pg_roles():
    sql = read_sql()

    assert re.findall(r"\bCREATE\b", sql) == ["CREATE"]
    assert re.search(
        r"SELECT 'CREATE ROLE my_finance_mcp' "
        r"WHERE NOT EXISTS \(SELECT FROM pg_roles "
        r"WHERE rolname = 'my_finance_mcp'\) \\gexec",
        sql,
    )


def test_role_gets_login_with_the_password_variable_and_no_literal():
    sql = read_sql()

    assert f"ALTER ROLE {ROLE} WITH LOGIN PASSWORD :'mcp_password';" in sql
    assert re.findall(r"PASSWORD\s+([^\s;]+)", sql) == [":'mcp_password'"]


def test_role_is_read_only_by_default():
    sql = read_sql()

    assert f"ALTER ROLE {ROLE} SET default_transaction_read_only = on;" in sql


def test_both_revokes_run_after_begin_and_before_the_first_grant():
    sql = read_sql()
    revoke_tables = f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE};"
    revoke_sequences = (
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {ROLE};"
    )

    begin = position(r"\bBEGIN;", sql)
    first_grant = position(r"\bGRANT\b", sql)

    assert re.findall(r"\bREVOKE\b[^;]*;", sql) == [
        revoke_tables,
        revoke_sequences,
    ]
    assert begin < sql.index(revoke_tables) < first_grant
    assert begin < sql.index(revoke_sequences) < first_grant


def test_commit_comes_after_the_last_grant():
    sql = read_sql()

    commit = position(r"\bCOMMIT;", sql)
    last_grant = sql.rindex(EXPECTED_GRANTS[-1])

    assert re.findall(r"\b(?:BEGIN|COMMIT);", sql) == ["BEGIN;", "COMMIT;"]
    assert last_grant < commit
    assert sql.endswith("COMMIT;")


def test_grants_are_exactly_the_three_read_only_ones():
    sql = read_sql()

    grants = re.findall(r"\bGRANT\b[^;]*;", sql)

    assert sorted(grants) == sorted(EXPECTED_GRANTS)


@pytest.mark.parametrize("pattern", FORBIDDEN_PATTERNS)
def test_script_never_mentions_write_or_forbidden_objects(pattern: str):
    sql = read_sql()

    assert re.search(pattern, sql, re.IGNORECASE) is None
