"""Mount that hands the sub-app only the paths it declares."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from my_finance_mcp.api.errors import register_error_handlers
from my_finance_mcp.server.mount import McpMount

METADATA_PATH = "/.well-known/oauth-protected-resource/mcp"

NOT_FOUND_BODY = {
    "error": {"code": "NOT_FOUND", "message": "Recurso não encontrado."}
}


def _sub_app_endpoint(request: Request) -> PlainTextResponse:
    return PlainTextResponse(f"sub-app {request.method} {request.url.path}")


def _client(sub_app_paths: list[str]) -> TestClient:
    sub_app = Starlette(
        routes=[
            Route(path, _sub_app_endpoint, methods=["GET", "POST"])
            for path in sub_app_paths
        ]
    )
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/x")
    def x():
        return {"route": "x"}

    app.router.routes.append(McpMount(sub_app))
    return TestClient(app)


@pytest.fixture
def client():
    return _client(["/mcp"])


def test_declared_path_reaches_the_sub_app(client):
    response = client.post("/mcp")

    assert response.status_code == 200
    assert response.text == "sub-app POST /mcp"


def test_fastapi_route_still_served_alongside_the_mount(client):
    response = client.get("/x")

    assert response.status_code == 200
    assert response.json() == {"route": "x"}


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_unknown_path_gets_the_fastapi_not_found_envelope(client, method):
    response = client.request(method, "/foo")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND_BODY


def test_wrong_method_on_fastapi_route_gets_method_not_allowed(client):
    response = client.post("/x")

    assert response.status_code == 405
    assert response.json() == {
        "error": {
            "code": "METHOD_NOT_ALLOWED",
            "message": "Método não permitido.",
        }
    }
    assert response.headers["allow"] == "GET"


def test_every_declared_path_is_served_by_the_mount():
    client = _client(["/mcp", METADATA_PATH])

    mcp = client.post("/mcp")
    metadata = client.get(METADATA_PATH)

    assert mcp.status_code == 200
    assert mcp.text == "sub-app POST /mcp"
    assert metadata.status_code == 200
    assert metadata.text == f"sub-app GET {METADATA_PATH}"


def test_path_below_a_declared_path_is_not_handed_to_the_sub_app():
    client = _client(["/mcp", METADATA_PATH])

    response = client.get("/.well-known/oauth-protected-resource")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND_BODY
