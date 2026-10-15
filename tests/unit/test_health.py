"""Liveness (/health) and readiness (/ready)."""

import logging
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from my_finance_mcp.api.errors import register_error_handlers
from my_finance_mcp.api.routes import health
from my_finance_mcp.api.routes.health import get_revision_reader, router
from my_finance_mcp.data.db import (
    DatabaseUnavailableError,
    SchemaUnreadableError,
)

HEALTH_LOGGER = "my_finance_mcp.api.routes.health"

DB_UNAVAILABLE_BODY = {
    "error": {
        "code": "DB_UNAVAILABLE",
        "message": "Banco de dados indisponível.",
    }
}
UNSUPPORTED_SCHEMA_BODY = {
    "error": {
        "code": "UNSUPPORTED_SCHEMA",
        "message": "Versão do schema do banco não suportada.",
    }
}


def _app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)
    return app


def _client_with_reader(reader) -> TestClient:
    app = _app()
    app.dependency_overrides[get_revision_reader] = lambda: reader
    return TestClient(app, raise_server_exceptions=False)


def _returning(revisions):
    return lambda: list(revisions)


def _raising(error: Exception):
    def reader():
        raise error

    return reader


def _warnings(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == HEALTH_LOGGER and record.levelno == logging.WARNING
    ]


def test_health_is_ok_without_touching_the_reader():
    calls = []

    def reader_must_not_run():
        calls.append("reader")
        raise AssertionError("/health called the revision reader")

    def dependency_must_not_run():
        calls.append("dependency")
        raise AssertionError("/health resolved the revision reader")

    app = _app()
    app.state.revision_reader = reader_must_not_run
    app.dependency_overrides[get_revision_reader] = dependency_must_not_run
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert calls == []


@pytest.mark.parametrize("revisions", [["0009"], ["0009", "0009"]])
def test_ready_with_supported_revisions(revisions):
    client = _client_with_reader(_returning(revisions))

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "schema_revisions": ["0009"]}


def test_ready_lists_distinct_revisions_in_order(monkeypatch):
    # A single supported revision cannot show ordering, so widen the set.
    monkeypatch.setattr(health, "SUPPORTED_SCHEMA_REVISIONS", ("0008", "0009"))
    client = _client_with_reader(_returning(["0009", "0008", "0009"]))

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "schema_revisions": ["0008", "0009"],
    }


def test_ready_when_database_is_unavailable():
    client = _client_with_reader(_raising(DatabaseUnavailableError()))

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == DB_UNAVAILABLE_BODY


@pytest.mark.parametrize("revisions", [[], ["0009", "0010"]])
def test_ready_with_unsupported_revisions_logs_them(revisions, caplog):
    client = _client_with_reader(_returning(revisions))

    with caplog.at_level(logging.WARNING, logger=HEALTH_LOGGER):
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == UNSUPPORTED_SCHEMA_BODY
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert repr(revisions) in warnings[0]


def test_ready_when_schema_is_unreadable():
    error = SchemaUnreadableError("permission denied for alembic_version")
    client = _client_with_reader(_raising(error))

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == UNSUPPORTED_SCHEMA_BODY


def test_get_revision_reader_returns_the_app_reader():
    sentinel = object()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(revision_reader=sentinel))
    )

    assert get_revision_reader(request) is sentinel


def test_ready_uses_the_app_reader_by_default():
    app = _app()
    app.state.revision_reader = _returning(["0009"])
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "schema_revisions": ["0009"]}


def test_routes_answer_without_authorization():
    client = _client_with_reader(_returning(["0009"]))

    health_response = client.get("/health")
    ready_response = client.get("/ready")

    assert "authorization" not in health_response.request.headers
    assert "authorization" not in ready_response.request.headers
    assert health_response.status_code == 200
    assert ready_response.status_code == 200
