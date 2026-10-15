"""App factory: fail-fast boot, wiring, shutdown and hidden docs."""

import base64
import logging
from types import SimpleNamespace

import psycopg
import pytest
from fastapi.testclient import TestClient

from my_finance_mcp import main
from my_finance_mcp.api.routes.health import get_revision_reader
from my_finance_mcp.config import Settings
from my_finance_mcp.data.db import CachedRevisionReader
from my_finance_mcp.main import create_app

CONFIG_LOGGER = "my_finance_mcp.config"

SETTINGS_VALUES = {
    # TEST-NET-1 address: never routed, so a real connect would hang.
    "DB_HOST": "192.0.2.1",
    "DB_PORT": 6543,
    "DB_NAME": "finance",
    "DB_USER": "my_finance_mcp",
    "DB_PASSWORD": "ZZSECRET-db-pass",
    "FERNET_KEY": base64.urlsafe_b64encode(
        b"ZZSECRET-fernet-key-32-bytes-ok!"
    ).decode(),
    "MCP_TOKEN_SECRET": "ZZSECRET-token-secret-at-least-32-bytes",
    "MCP_PUBLIC_URL": "https://finance-mcp.x.ts.net/mcp",
    "MCP_ALLOWED_HOSTS": ["finance-mcp.x.ts.net"],
}


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def restore_root_logger():
    root = logging.getLogger()
    level, handlers = root.level, list(root.handlers)
    yield
    root.setLevel(level)
    root.handlers[:] = handlers


@pytest.fixture
def connect_attempts(monkeypatch):
    attempts = []

    def refuse_connect(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("the app opened a database connection")

    monkeypatch.setattr(psycopg, "connect", refuse_connect)
    return attempts


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**SETTINGS_VALUES, **overrides})


def test_create_app_without_settings_exits_on_empty_env(caplog):
    caplog.set_level(logging.CRITICAL, logger=CONFIG_LOGGER)

    with pytest.raises(SystemExit) as exc_info:
        create_app()

    assert exc_info.value.code == 1
    critical = [
        record.getMessage()
        for record in caplog.records
        if record.name == CONFIG_LOGGER and record.levelno == logging.CRITICAL
    ]
    assert "DB_HOST: obrigatória" in critical


def test_boot_with_unreachable_database_does_not_connect(connect_attempts):
    app = create_app(_settings())

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert connect_attempts == []


def test_leaving_the_app_disposes_the_engine(monkeypatch):
    app = create_app(_settings())
    engine = app.state.engine
    disposed = []
    monkeypatch.setattr(
        engine, "dispose", lambda *args, **kwargs: disposed.append(args)
    )

    with TestClient(app):
        assert disposed == []

    assert disposed == [()]


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_routes_are_not_exposed(path):
    client = TestClient(create_app(_settings()))

    response = client.get(path)

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "NOT_FOUND", "message": "Recurso não encontrado."}
    }


def test_revision_reader_is_cached_for_thirty_seconds():
    app = create_app(_settings())

    reader = app.state.revision_reader
    assert isinstance(reader, CachedRevisionReader)
    assert reader.ttl_seconds == 30


def test_ready_reads_the_app_engine_once_within_the_cache(monkeypatch):
    reads = []

    def spy_read(engine):
        reads.append(engine)
        return ["0009"]

    monkeypatch.setattr(main, "read_schema_revisions", spy_read)
    app = create_app(_settings())
    client = TestClient(app)

    first = client.get("/ready")
    second = client.get("/ready")

    expected = {"status": "ready", "schema_revisions": ["0009"]}
    assert first.status_code == 200
    assert first.json() == expected
    assert second.status_code == 200
    assert second.json() == expected
    assert len(reads) == 1
    assert reads[0] is app.state.engine


def test_get_revision_reader_returns_the_app_reader():
    app = create_app(_settings())

    assert get_revision_reader(SimpleNamespace(app=app)) is (
        app.state.revision_reader
    )


@pytest.mark.parametrize(
    "level, expected", [("DEBUG", logging.DEBUG), ("ERROR", logging.ERROR)]
)
def test_logging_uses_the_configured_level(level, expected):
    create_app(_settings(LOG_LEVEL=level))

    assert logging.getLogger().level == expected
