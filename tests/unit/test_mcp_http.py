"""MCP endpoint mounted in the app factory, over the real lifespan."""

import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from my_finance_mcp import main
from my_finance_mcp.config import Settings
from my_finance_mcp.data.db import DatabaseUnavailableError
from my_finance_mcp.main import create_app
from tests.unit.test_main import _settings

ALLOWED_HOST = "finance-mcp.x.ts.net"

# Stateless mode needs no initialize: each POST is served by a fresh,
# born-ready transport. The version header is optional, but when present it
# must be a revision the SDK accepts for the handshake era.
TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-11-25",
}
TOOLS_LIST_RESULT = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}

MAX_BODY_BYTES = 65536

NOT_FOUND_BODY = {
    "error": {"code": "NOT_FOUND", "message": "Recurso não encontrado."}
}
METHOD_NOT_ALLOWED_BODY = {
    "error": {"code": "METHOD_NOT_ALLOWED", "message": "Método não permitido."}
}


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


def _client(settings: Settings | None = None) -> tuple[TestClient, list]:
    """Client over create_app, with the MCP tools/list method spied on."""
    app = create_app(settings or _settings())
    mcp = app.state.mcp
    original_list_tools = mcp.list_tools
    calls = []

    async def spy_list_tools():
        calls.append("tools/list")
        return await original_list_tools()

    mcp.list_tools = spy_list_tools
    return TestClient(app), calls


def _post_mcp(client: TestClient, host: str = ALLOWED_HOST, **headers):
    return client.post(
        "/mcp",
        content=json.dumps(TOOLS_LIST).encode(),
        headers={**MCP_HEADERS, "Host": host, **headers},
    )


def _tools_list_body(size: int) -> bytes:
    raw = json.dumps(TOOLS_LIST).encode()
    # Whitespace before the closing brace keeps the JSON-RPC body valid.
    return raw[:-1] + b" " * (size - len(raw)) + b"}"


def test_tools_list_over_http_returns_the_result():
    client, calls = _client()

    with client:
        response = _post_mcp(client)

    assert response.status_code == 200
    assert response.json() == TOOLS_LIST_RESULT
    assert calls == ["tools/list"]


def test_mcp_response_has_no_session_id():
    client, _ = _client()

    with client:
        response = _post_mcp(client)

    assert response.status_code == 200
    assert "mcp-session-id" not in response.headers


def test_mcp_response_is_json():
    client, _ = _client()

    with client:
        response = _post_mcp(client)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"


def test_health_answers_outside_the_mcp_host_allowlist():
    client, _ = _client()

    with client:
        response = client.get("/health", headers={"Host": "localhost:8000"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_answers_outside_the_mcp_host_allowlist(monkeypatch):
    monkeypatch.setattr(main, "read_schema_revisions", lambda engine: ["0009"])
    client, _ = _client()

    with client:
        response = client.get("/ready", headers={"Host": "localhost:8000"})

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "schema_revisions": ["0009"]}


def test_ready_failure_keeps_its_envelope_with_the_mcp_mounted(monkeypatch):
    def unavailable(engine):
        raise DatabaseUnavailableError()

    monkeypatch.setattr(main, "read_schema_revisions", unavailable)
    client, _ = _client()

    with client:
        response = client.get("/ready", headers={"Host": "localhost:8000"})

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "DB_UNAVAILABLE",
            "message": "Banco de dados indisponível.",
        }
    }


@pytest.mark.parametrize(
    "allowed_hosts, host",
    [
        ([ALLOWED_HOST], "evil.example"),
        ([ALLOWED_HOST], "finance-mcp.x.ts.net.evil.example"),
        (["finance-mcp.x.ts.net:*"], "finance-mcp.x.ts.net"),
    ],
)
def test_host_outside_the_allowlist_is_misdirected(allowed_hosts, host):
    client, calls = _client(_settings(MCP_ALLOWED_HOSTS=allowed_hosts))

    with client:
        response = _post_mcp(client, host=host)

    assert response.status_code == 421
    assert calls == []


def test_host_with_port_matches_a_wildcard_port_item():
    client, calls = _client(
        _settings(MCP_ALLOWED_HOSTS=["finance-mcp.x.ts.net:*"])
    )

    with client:
        response = _post_mcp(client, host="finance-mcp.x.ts.net:443")

    assert response.status_code == 200
    assert response.json() == TOOLS_LIST_RESULT
    assert calls == ["tools/list"]


def test_origin_outside_the_allowlist_is_forbidden():
    client, calls = _client()

    with client:
        response = _post_mcp(client, Origin="https://evil.example")

    assert response.status_code == 403
    assert calls == []


def test_origin_in_the_allowlist_is_processed():
    client, calls = _client(
        _settings(MCP_ALLOWED_ORIGINS=["https://finance-mcp.x.ts.net"])
    )

    with client:
        response = _post_mcp(client, Origin="https://finance-mcp.x.ts.net")

    assert response.status_code == 200
    assert response.json() == TOOLS_LIST_RESULT
    assert calls == ["tools/list"]


def test_body_over_the_limit_is_rejected():
    body = _tools_list_body(MAX_BODY_BYTES + 1)
    client, calls = _client()

    with client:
        response = client.post(
            "/mcp", content=body, headers={**MCP_HEADERS, "Host": ALLOWED_HOST}
        )

    assert len(body) == 65537
    assert response.status_code == 413
    assert calls == []


def test_body_at_the_limit_is_not_rejected():
    body = _tools_list_body(MAX_BODY_BYTES)
    client, calls = _client()

    with client:
        response = client.post(
            "/mcp", content=body, headers={**MCP_HEADERS, "Host": ALLOWED_HOST}
        )

    assert len(body) == 65536
    assert response.status_code == 200
    assert calls == ["tools/list"]


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_unknown_path_returns_the_not_found_envelope(method):
    client, _ = _client()

    with client:
        response = client.request(method, "/foo")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND_BODY


@pytest.mark.parametrize("path", ["/health", "/ready"])
def test_post_on_health_routes_is_method_not_allowed(path):
    client, _ = _client()

    with client:
        response = client.post(path)

    assert response.status_code == 405
    assert response.json() == METHOD_NOT_ALLOWED_BODY
    assert response.headers["allow"] == "GET"


def _spy_shutdown(monkeypatch, app, events, fail: bool = False):
    session_manager = app.state.mcp.session_manager
    original_run = session_manager.run

    @asynccontextmanager
    async def spy_run():
        async with original_run():
            yield
        events.append("session manager closed")
        if fail:
            raise RuntimeError("session manager shutdown failed")

    monkeypatch.setattr(session_manager, "run", spy_run)
    monkeypatch.setattr(
        app.state.engine,
        "dispose",
        lambda *args, **kwargs: events.append("engine disposed"),
    )


def test_shutdown_closes_the_session_manager_before_disposing(monkeypatch):
    app = create_app(_settings())
    events = []
    _spy_shutdown(monkeypatch, app, events)

    with TestClient(app) as client:
        # The real session manager is running: requests reach the server.
        response = _post_mcp(client)
        assert response.status_code == 200
        assert events == []

    assert events == ["session manager closed", "engine disposed"]


def test_engine_is_disposed_when_the_session_manager_fails(monkeypatch):
    app = create_app(_settings())
    events = []
    _spy_shutdown(monkeypatch, app, events, fail=True)

    with pytest.raises(RuntimeError, match="session manager shutdown failed"):
        with TestClient(app):
            pass

    assert events == ["session manager closed", "engine disposed"]
