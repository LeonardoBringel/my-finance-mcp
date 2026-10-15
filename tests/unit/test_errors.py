"""Unified HTTP error envelope."""

import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from my_finance_mcp.api.errors import (
    ApiError,
    error_body,
    register_error_handlers,
)

MARKER = "marker-secret-text"


@pytest.fixture
def client():
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    def boom():
        raise RuntimeError(MARKER)

    @app.get("/items/{item_id}")
    def get_item(item_id: int):
        return {"item_id": item_id}

    @app.post("/only-post")
    def only_post():
        return {}

    @app.get("/api-error")
    def api_error():
        raise ApiError(503, "CUSTOM_CODE", "Mensagem própria.")

    @app.get("/teapot")
    def teapot():
        raise HTTPException(status_code=418, detail="internal detail")

    return TestClient(app, raise_server_exceptions=False)


def _assert_error_body(response, status, code, message):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body == {"error": {"code": code, "message": message}}


def test_unknown_route_returns_not_found(client):
    response = client.get("/nao-existe")

    _assert_error_body(response, 404, "NOT_FOUND", "Recurso não encontrado.")


def test_wrong_method_returns_method_not_allowed_with_allow_header(client):
    response = client.get("/only-post")

    _assert_error_body(
        response, 405, "METHOD_NOT_ALLOWED", "Método não permitido."
    )
    assert response.headers["allow"] == "POST"


def test_invalid_parameter_returns_invalid_request(client):
    response = client.get("/items/not-a-number")

    _assert_error_body(response, 422, "INVALID_REQUEST", "Requisição inválida.")


def test_other_http_exception_uses_generic_code_without_detail(client):
    response = client.get("/teapot")

    _assert_error_body(response, 418, "HTTP_ERROR", "Erro na requisição.")
    assert "internal detail" not in response.text


def test_api_error_keeps_its_status_code_and_message(client):
    response = client.get("/api-error")

    _assert_error_body(response, 503, "CUSTOM_CODE", "Mensagem própria.")


def test_unhandled_exception_returns_generic_500_and_logs_traceback(
    client, caplog
):
    with caplog.at_level(logging.ERROR):
        response = client.get("/boom")

    _assert_error_body(response, 500, "INTERNAL", "Erro interno.")
    assert MARKER not in response.text
    assert "RuntimeError" not in response.text
    assert "Traceback" in caplog.text
    assert f"RuntimeError: {MARKER}" in caplog.text


def test_error_body_builds_the_envelope():
    assert error_body("X", "m") == {"error": {"code": "X", "message": "m"}}
