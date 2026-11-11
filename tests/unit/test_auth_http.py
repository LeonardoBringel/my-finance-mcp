"""Personal token required on /mcp, over create_app and the real lifespan."""

import json
import time
import warnings
from functools import partial

import jwt
import pytest
from fastapi.testclient import TestClient
from mcp.shared.exceptions import MCPDeprecationWarning
from pydantic import BaseModel

from my_finance_mcp import main
from my_finance_mcp.auth.identity import AuthenticatedUser, current_user
from my_finance_mcp.auth.tokens import issue_token
from my_finance_mcp.config import Settings
from my_finance_mcp.data.db import DatabaseUnavailableError
from my_finance_mcp.main import create_app
from my_finance_mcp.server.app import build_mcp_server
from my_finance_mcp.server.runtime import ToolRuntime
from tests.unit.test_main import _settings
from tests.unit.test_mcp_http import (
    ALLOWED_HOST,
    MAX_BODY_BYTES,
    MCP_HEADERS,
    TOOLS_LIST,
    TOOLS_LIST_RESULT,
    _client,
    _listed_tools,
    _tools_list_body,
)

USER_ID = 42
TOKEN_VERSION = 3
OTHER_USER_ID = 7
OTHER_TOKEN_VERSION = 5

WWW_AUTHENTICATE = (
    'Bearer error="invalid_token", error_description="Authentication '
    'required", resource_metadata="https://finance-mcp.x.ts.net/'
    '.well-known/oauth-protected-resource/mcp"'
)
UNAUTHORIZED_BODY = {
    "error": "invalid_token",
    "error_description": "Authentication required",
}
METADATA_PATH = "/.well-known/oauth-protected-resource/mcp"

WHOAMI = "whoami"
WHOAMI_CALL = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {"name": WHOAMI, "arguments": {}},
}


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def versions(monkeypatch):
    """users.token_version by id, read by the lookup create_app wires in."""
    table = {USER_ID: TOKEN_VERSION, OTHER_USER_ID: OTHER_TOKEN_VERSION}

    def token_version(engine, user_id):
        return table.get(user_id)

    monkeypatch.setattr(main, "get_token_version", token_version)
    return table


def _token(
    user_id: int = USER_ID,
    token_version: int = TOKEN_VERSION,
    settings: Settings | None = None,
) -> str:
    return issue_token(
        settings or _settings(), user_id, token_version, 1, time.time()
    )


def _post(
    client: TestClient,
    payload: dict = TOOLS_LIST,
    host: str = ALLOWED_HOST,
    **headers,
):
    return client.post(
        "/mcp",
        content=json.dumps(payload).encode(),
        headers={**MCP_HEADERS, "Host": host, **headers},
    )


def _assert_unauthorized(response) -> None:
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == WWW_AUTHENTICATE
    assert response.json() == UNAUTHORIZED_BODY


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-header"),
        pytest.param(
            {"Authorization": "Basic YWxpY2U6c2VjcmV0"}, id="basic-scheme"
        ),
        pytest.param({"Authorization": "Bearer "}, id="empty-bearer"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="garbage"),
    ],
)
def test_mcp_without_a_usable_token_is_unauthorized(versions, headers):
    client, calls = _client()

    with client:
        response = _post(client, **headers)

    _assert_unauthorized(response)
    assert calls == []


def test_mcp_with_a_token_for_another_audience_is_unauthorized(versions):
    other = _settings(MCP_PUBLIC_URL="https://other-mcp.x.ts.net/mcp")
    client, calls = _client()

    with client:
        response = _post(
            client, Authorization=f"Bearer {_token(settings=other)}"
        )

    _assert_unauthorized(response)
    assert calls == []


def test_mcp_with_a_revoked_token_is_unauthorized(versions):
    revoked = _token(token_version=TOKEN_VERSION - 1)
    client, calls = _client()

    with client:
        response = _post(client, Authorization=f"Bearer {revoked}")

    _assert_unauthorized(response)
    assert calls == []


def test_mcp_with_a_token_of_an_unknown_user_is_unauthorized(versions):
    client, calls = _client()

    with client:
        response = _post(client, Authorization=f"Bearer {_token(user_id=99)}")

    _assert_unauthorized(response)
    assert calls == []


@pytest.mark.parametrize(
    ("name", "scheme"),
    [("Authorization", "Bearer"), ("authorization", "bearer")],
)
def test_mcp_with_a_valid_token_runs_tools_list(versions, name, scheme):
    client, calls = _client()

    with client:
        response = _post(client, **{name: f"{scheme} {_token()}"})

    assert response.status_code == 200
    assert _listed_tools(response) == TOOLS_LIST_RESULT
    assert calls == ["tools/list"]


def test_lookup_reads_the_app_engine_with_the_token_user(monkeypatch):
    reads = []

    def token_version(engine, user_id):
        reads.append((engine, user_id))
        return TOKEN_VERSION

    monkeypatch.setattr(main, "get_token_version", token_version)
    client, _ = _client()

    with client:
        response = _post(client, Authorization=f"Bearer {_token()}")

    assert response.status_code == 200
    assert reads == [(client.app.state.engine, USER_ID)]


def test_revocation_takes_effect_on_the_next_request(versions):
    token = _token()
    client, calls = _client()

    with client:
        accepted = _post(client, Authorization=f"Bearer {token}")
        versions[USER_ID] = TOKEN_VERSION + 1
        rejected = _post(client, Authorization=f"Bearer {token}")

    assert accepted.status_code == 200
    _assert_unauthorized(rejected)
    assert calls == ["tools/list"]


def test_protected_resource_metadata_is_public(versions):
    client, _ = _client()

    with client:
        response = client.get(METADATA_PATH, headers={"Host": ALLOWED_HOST})

    assert response.status_code == 200
    body = response.json()
    assert body["resource"] == "https://finance-mcp.x.ts.net/mcp"
    assert [url.rstrip("/") for url in body["authorization_servers"]] == [
        "https://finance-mcp.x.ts.net"
    ]
    assert body["scopes_supported"] == ["finance:read"]
    assert body["bearer_methods_supported"] == ["header"]


def test_host_outside_the_allowlist_with_a_valid_token_is_misdirected(
    versions,
):
    client, calls = _client()

    with client:
        response = _post(
            client, host="evil.example", Authorization=f"Bearer {_token()}"
        )

    assert response.status_code == 421
    assert calls == []


def test_host_outside_the_allowlist_without_a_token_is_unauthorized(
    versions,
):
    client, calls = _client()

    with client:
        response = _post(client, host="evil.example")

    _assert_unauthorized(response)
    assert calls == []


@pytest.mark.parametrize(
    ("with_token", "expected"), [(True, 413), (False, 401)]
)
def test_body_over_the_limit_needs_a_token_to_reach_the_size_check(
    versions, with_token, expected
):
    body = _tools_list_body(MAX_BODY_BYTES + 1)
    auth = {"Authorization": f"Bearer {_token()}"} if with_token else {}
    client, calls = _client()

    with client:
        response = client.post(
            "/mcp",
            content=body,
            headers={**MCP_HEADERS, "Host": ALLOWED_HOST, **auth},
        )

    assert len(body) == 65537
    assert response.status_code == expected
    if expected == 401:
        _assert_unauthorized(response)
    assert calls == []


def test_health_needs_no_token(versions):
    client, _ = _client()

    with client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_needs_no_token(versions, monkeypatch):
    monkeypatch.setattr(main, "read_schema_revisions", lambda engine: ["0009"])
    client, _ = _client()

    with client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "schema_revisions": ["0009"]}


def test_ready_failure_needs_no_token(versions, monkeypatch):
    def unavailable(engine):
        raise DatabaseUnavailableError()

    monkeypatch.setattr(main, "read_schema_revisions", unavailable)
    client, _ = _client()

    with client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "DB_UNAVAILABLE",
            "message": "Banco de dados indisponível.",
        }
    }


def test_create_app_with_auth_emits_no_deprecation_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        create_app(_settings())

    deprecations = [
        warning
        for warning in caught
        if issubclass(warning.category, MCPDeprecationWarning)
    ]
    assert deprecations == []


def test_unexpected_lookup_failure_is_a_server_error(monkeypatch):
    def broken(engine, user_id):
        raise RuntimeError("lookup bug")

    monkeypatch.setattr(main, "get_token_version", broken)
    app_client, calls = _client()
    client = TestClient(app_client.app, raise_server_exceptions=False)

    with client:
        response = _post(client, Authorization=f"Bearer {_token()}")

    assert response.status_code == 500
    assert calls == []


class Identity(BaseModel):
    user_id: int
    jti: str


class WhoamiModule:
    """A synchronous tool that reports the identity seen in its handler."""

    def __init__(self) -> None:
        self.seen: list[AuthenticatedUser] = []

    def register(self, finance_tool, runtime: ToolRuntime) -> None:
        seen = self.seen

        @finance_tool
        def whoami() -> Identity:
            """Returns the caller identity."""
            user = current_user()
            seen.append(user)
            return Identity(user_id=user.user_id, jti=user.jti)


def _jti(token: str) -> str:
    return jwt.decode(token, options={"verify_signature": False})["jti"]


def test_tool_handler_sees_the_token_identity(versions, monkeypatch):
    module = WhoamiModule()
    monkeypatch.setattr(
        main,
        "build_mcp_server",
        partial(build_mcp_server, tool_modules=[module]),
    )
    token = _token()
    client = TestClient(create_app(_settings()))

    with client:
        response = _post(client, WHOAMI_CALL, Authorization=f"Bearer {token}")

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["structuredContent"] == {
        "user_id": USER_ID,
        "jti": _jti(token),
    }
    assert module.seen == [AuthenticatedUser(user_id=USER_ID, jti=_jti(token))]


def test_each_call_sees_its_own_token_user(versions, monkeypatch):
    module = WhoamiModule()
    monkeypatch.setattr(
        main,
        "build_mcp_server",
        partial(build_mcp_server, tool_modules=[module]),
    )
    first_token = _token()
    second_token = _token(OTHER_USER_ID, OTHER_TOKEN_VERSION)
    client = TestClient(create_app(_settings()))

    with client:
        first = _post(
            client, WHOAMI_CALL, Authorization=f"Bearer {first_token}"
        )
        second = _post(
            client, WHOAMI_CALL, Authorization=f"Bearer {second_token}"
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["result"]["structuredContent"]["user_id"] == USER_ID
    assert second.json()["result"]["structuredContent"]["user_id"] == (
        OTHER_USER_ID
    )
    assert module.seen == [
        AuthenticatedUser(user_id=USER_ID, jti=_jti(first_token)),
        AuthenticatedUser(user_id=OTHER_USER_ID, jti=_jti(second_token)),
    ]
