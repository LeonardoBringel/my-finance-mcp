"""my-finance-mcp-token issue."""

import hashlib
import hmac
import logging
import re
import time

import anyio
import jwt
import pytest

from my_finance_mcp import cli
from my_finance_mcp.auth.verifier import PersonalTokenVerifier
from my_finance_mcp.config import Settings
from my_finance_mcp.data.db import DatabaseUnavailableError
from my_finance_mcp.data.repositories.users_repository import UserVersion

SECRET = "ZZSECRET-token-secret-at-least-32-bytes"
PUBLIC_URL = "https://finance-mcp.x.ts.net/mcp"
FERNET_KEY = "WlpTRUNSRVQtZmVybmV0LWtleS0zMi1ieXRlcy1vayE="
# The username_hash vector the spec fixes for "alice" under FERNET_KEY.
ALICE_HASH = "58bdcfa5c47eb8f88d243d53939c2f5e7600bcfa9c8ad9d509e7c601917f4d95"
DAY = 86400

CONFIG_LOGGER = "my_finance_mcp.config"

SETTINGS_VALUES = {
    "DB_HOST": "db.internal",
    "DB_PORT": 6543,
    "DB_NAME": "finance",
    "DB_USER": "my_finance_mcp",
    "DB_PASSWORD": "ZZSECRET-db-pass",
    "FERNET_KEY": FERNET_KEY,
    "MCP_TOKEN_SECRET": SECRET,
    "MCP_PUBLIC_URL": PUBLIC_URL,
    "MCP_ALLOWED_HOSTS": ["finance-mcp.x.ts.net"],
}

REQUIRED = [
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "FERNET_KEY",
    "MCP_TOKEN_SECRET",
    "MCP_PUBLIC_URL",
    "MCP_ALLOWED_HOSTS",
]

ISSUE_ALICE = ["issue", "--username", "alice", "--days", "30"]


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    # Never read the developer's .env or inherit their shell variables.
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**SETTINGS_VALUES, **overrides})


class FakeEngine:
    def __init__(self):
        self.dispose_calls = 0

    def dispose(self):
        self.dispose_calls += 1


class FakeDatabase:
    """Doubles create_db_engine and find_user_by_username_hash."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.engines: list[FakeEngine] = []
        self.engine_settings: list[Settings] = []
        self.lookups: list[tuple[FakeEngine, str]] = []

    def create_db_engine(self, settings):
        engine = FakeEngine()
        self.engine_settings.append(settings)
        self.engines.append(engine)
        return engine

    def find_user_by_username_hash(self, engine, username_hash):
        self.lookups.append((engine, username_hash))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def use_settings(monkeypatch, settings):
    def install(value: Settings = settings):
        monkeypatch.setattr(cli, "load_settings_or_exit", lambda: value)
        return value

    install()
    return install


@pytest.fixture
def database(monkeypatch):
    def install(outcome) -> FakeDatabase:
        fake = FakeDatabase(outcome)
        monkeypatch.setattr(cli, "create_db_engine", fake.create_db_engine)
        monkeypatch.setattr(
            cli, "find_user_by_username_hash", fake.find_user_by_username_hash
        )
        return fake

    return install


@pytest.fixture
def found(database) -> FakeDatabase:
    return database(UserVersion(user_id=42, token_version=3))


def read_claims(token: str) -> dict:
    return jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        audience=PUBLIC_URL,
        options={"verify_exp": False, "verify_iat": False},
    )


def issued_token(capsys, argv=ISSUE_ALICE) -> tuple[str, str]:
    """Runs a successful issue and returns the stdout token and stderr."""
    assert cli.main(argv) == 0
    out, err = capsys.readouterr()
    assert out.count("\n") == 1 and out.endswith("\n")
    return out[:-1], err


def run_verify(verifier, token):
    async def main():
        return await verifier.verify_token(token)

    return anyio.run(main)


# Entry point (AUTH-37)


def test_help_exits_zero_without_configuration(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["--help"])

    assert info.value.code == 0
    assert "issue" in capsys.readouterr().out


# Successful issue (AUTH-38, AUTH-39, AUTH-42)


@pytest.mark.usefixtures("use_settings")
def test_issue_prints_one_token_line_with_user_claims(found, capsys):
    before = int(time.time())
    token, _ = issued_token(capsys)
    after = int(time.time())

    claims = read_claims(token)
    assert claims["sub"] == "42"
    assert claims["tv"] == 3
    assert claims["exp"] - claims["iat"] == 30 * DAY
    assert before <= claims["iat"] <= after


@pytest.mark.usefixtures("use_settings")
def test_issue_writes_exact_summary_to_stderr(found, capsys):
    token, err = issued_token(capsys)

    exp = read_claims(token)["exp"]
    expected = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exp))
    assert err == f"Token emitido para o usuário 42, válido até {expected}.\n"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", expected)


@pytest.mark.usefixtures("use_settings")
def test_issue_looks_up_the_spec_hash_of_the_username(found, capsys):
    issued_token(capsys)

    assert found.lookups == [(found.engines[0], ALICE_HASH)]


@pytest.mark.usefixtures("use_settings")
def test_username_is_hashed_as_typed_without_strip(found, capsys):
    issued_token(capsys, ["issue", "--username", " alice", "--days", "30"])

    expected = hmac.new(
        FERNET_KEY.encode("utf-8"), b" alice", hashlib.sha256
    ).hexdigest()
    assert [username_hash for _, username_hash in found.lookups] == [expected]
    assert expected != ALICE_HASH


@pytest.mark.usefixtures("use_settings")
def test_engine_is_created_from_loaded_settings(found, settings, capsys):
    issued_token(capsys)

    assert found.engine_settings == [settings]


@pytest.mark.usefixtures("use_settings")
def test_without_days_issues_for_max_days(found, capsys):
    token, _ = issued_token(capsys, ["issue", "--username", "alice"])

    claims = read_claims(token)
    assert claims["exp"] - claims["iat"] == 90 * DAY


def test_without_days_follows_configured_max_days(use_settings, found, capsys):
    use_settings(make_settings(MCP_TOKEN_MAX_DAYS=30))

    token, _ = issued_token(capsys, ["issue", "--username", "alice"])

    claims = read_claims(token)
    assert claims["exp"] - claims["iat"] == 30 * DAY


@pytest.mark.usefixtures("use_settings")
@pytest.mark.parametrize("days", [1, 90])
def test_days_at_range_limits_is_issued(found, capsys, days):
    argv = ["issue", "--username", "alice", "--days", str(days)]

    token, _ = issued_token(capsys, argv)

    claims = read_claims(token)
    assert claims["exp"] - claims["iat"] == days * DAY


# Invalid arguments (AUTH-40, AUTH-41)


@pytest.mark.usefixtures("use_settings")
@pytest.mark.parametrize("days", ["0", "91", "abc", "-1", "1.5", ""])
def test_invalid_days_exits_2_without_database(found, capsys, days):
    code = cli.main(["issue", "--username", "alice", "--days", days])

    assert code == 2
    assert capsys.readouterr() == ("", "--days deve estar entre 1 e 90.\n")
    assert found.engines == []
    assert found.lookups == []


def test_days_above_configured_max_exits_2(use_settings, found, capsys):
    use_settings(make_settings(MCP_TOKEN_MAX_DAYS=30))

    code = cli.main(["issue", "--username", "alice", "--days", "31"])

    assert code == 2
    assert capsys.readouterr() == ("", "--days deve estar entre 1 e 30.\n")
    assert found.engines == []
    assert found.lookups == []


@pytest.mark.usefixtures("use_settings")
@pytest.mark.parametrize("username", ["", "   "])
def test_blank_username_exits_2_without_database(found, capsys, username):
    code = cli.main(["issue", "--username", username, "--days", "30"])

    assert code == 2
    assert capsys.readouterr() == ("", "--username é obrigatório.\n")
    assert found.engines == []
    assert found.lookups == []


# Lookup failures (AUTH-43, AUTH-44)


@pytest.mark.usefixtures("use_settings")
def test_unknown_user_exits_1(database, capsys):
    database(None)

    assert cli.main(ISSUE_ALICE) == 1
    assert capsys.readouterr() == ("", "Usuário não encontrado.\n")


@pytest.mark.usefixtures("use_settings")
def test_database_unavailable_exits_1(database, capsys):
    database(DatabaseUnavailableError())

    assert cli.main(ISSUE_ALICE) == 1
    assert capsys.readouterr() == ("", "Banco de dados indisponível.\n")


@pytest.mark.usefixtures("use_settings")
@pytest.mark.parametrize(
    "outcome",
    [
        UserVersion(user_id=42, token_version=3),
        None,
        DatabaseUnavailableError(),
    ],
    ids=["issued", "not_found", "db_unavailable"],
)
def test_engine_is_disposed_once(database, outcome):
    fake = database(outcome)

    cli.main(ISSUE_ALICE)

    assert [engine.dispose_calls for engine in fake.engines] == [1]


# Invalid configuration (AUTH-45)


def test_invalid_configuration_exits_1_before_engine(found, caplog, capsys):
    caplog.set_level(logging.CRITICAL, logger=CONFIG_LOGGER)

    with pytest.raises(SystemExit) as info:
        cli.main(ISSUE_ALICE)

    assert info.value.code == 1
    critical = [
        record.getMessage()
        for record in caplog.records
        if record.name == CONFIG_LOGGER and record.levelno == logging.CRITICAL
    ]
    assert critical == [f"{name}: obrigatória" for name in REQUIRED]
    assert found.engines == []
    assert found.lookups == []
    assert capsys.readouterr().out == ""


# Nothing sensitive leaks (AUTH-46)


@pytest.mark.usefixtures("use_settings")
@pytest.mark.parametrize(
    "outcome",
    [
        UserVersion(user_id=42, token_version=3),
        None,
        DatabaseUnavailableError(),
    ],
    ids=["issued", "not_found", "db_unavailable"],
)
def test_username_and_token_stay_out_of_logs_stderr_and_files(
    database, outcome, caplog, capsys, tmp_path
):
    caplog.set_level(logging.DEBUG)
    database(outcome)

    cli.main(ISSUE_ALICE)

    out, err = capsys.readouterr()
    token = out.strip()
    assert "alice" not in err
    for record in caplog.records:
        message = record.getMessage()
        assert "alice" not in message
        if token:
            assert token not in message
            assert all(part not in message for part in token.split("."))
    assert list(tmp_path.iterdir()) == []


# Revocation by token_version (AUTH-47)


@pytest.mark.usefixtures("use_settings")
def test_issued_token_passes_verifier_while_version_is_unchanged(
    found, settings, capsys
):
    token, _ = issued_token(capsys)

    result = run_verify(
        PersonalTokenVerifier(settings, lambda user_id: 3), token
    )

    assert result is not None
    assert result.token == token
    assert result.subject == "42"


@pytest.mark.usefixtures("use_settings")
def test_issued_token_is_revoked_after_version_changes(
    found, settings, capsys, caplog
):
    caplog.set_level(logging.INFO)
    token, _ = issued_token(capsys)

    result = run_verify(
        PersonalTokenVerifier(settings, lambda user_id: 4), token
    )

    assert result is None
    assert [record.getMessage() for record in caplog.records] == [
        "token rejected reason=revoked"
    ]
