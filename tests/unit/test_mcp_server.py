"""MCP server factory, tool registration and transport app."""

import base64
import importlib.metadata
import json
import logging
from datetime import date
from pathlib import Path

import anyio
import jsonschema
import pytest
from mcp import Client
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel

from my_finance_mcp.auth.identity import AuthenticatedUser, current_user
from my_finance_mcp.config import Settings
from my_finance_mcp.server.app import (
    MAX_MCP_BODY_BYTES,
    TOOL_MODULES,
    build_mcp_server,
    create_mcp_app,
)
from my_finance_mcp.server.runtime import (
    ToolInputError,
    ToolOutput,
    ToolRuntime,
)
from my_finance_mcp.server.schemas.envelope import ToolResult

SRC = Path(__file__).resolve().parents[2] / "src"
INSTRUCTIONS_FILE = SRC / "my_finance_mcp/server/guides/instructions.md"
TOOL_NAME = "sample_tool"
MARKERS = ["ZZDESC123", "987654.32", "ZZHINT456", "ZZMSG321"]

SETTINGS_VALUES = {
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
    "MCP_ALLOWED_HOSTS": ["finance-mcp.x.ts.net:*", "localhost:*"],
    "MCP_ALLOWED_ORIGINS": ["https://claude.ai"],
}


class SampleData(BaseModel):
    amount: float
    label: str


class FixedClock:
    def today(self) -> date:
        return date(2026, 10, 8)


def fixed_identity() -> AuthenticatedUser:
    return AuthenticatedUser(user_id=42, jti="j-1")


def ok_handler(ctx, args):
    return ToolOutput(data=SampleData(amount=987654.32, label="total"))


def input_error_handler(ctx, args):
    raise ToolInputError(
        "UNKNOWN_CATEGORY", "Categoria ZZMSG321 não encontrada.", "ZZHINT456"
    )


def unexpected_handler(ctx, args):
    raise RuntimeError("ZZSECRET789")


class SampleModule:
    """A tool module registered through the production path."""

    def __init__(self, handler) -> None:
        self.handler = handler

    def register(self, finance_tool, runtime: ToolRuntime) -> None:
        handler = self.handler

        @finance_tool
        def sample_tool(description: str) -> ToolResult[SampleData]:
            """Sample tool used by the tests."""
            return runtime.run(
                TOOL_NAME, description, handler, ToolResult[SampleData]
            )


def make_runtime(identity=fixed_identity) -> ToolRuntime:
    return ToolRuntime(FixedClock(), identity=identity)


def with_client(server, action):
    async def main():
        async with Client(server) as client:
            return await action(client)

    return anyio.run(main)


def list_tools(server):
    async def action(client):
        return (await client.list_tools()).tools

    return with_client(server, action)


def call_sample(server, arguments=None):
    async def action(client):
        await client.list_tools()
        return await client.call_tool(
            TOOL_NAME, arguments or {"description": "ZZDESC123"}
        )

    return with_client(server, action)


def sample_server(handler=ok_handler, identity=fixed_identity):
    return build_mcp_server(
        make_runtime(identity), tool_modules=(SampleModule(handler),)
    )


def test_production_catalog_is_empty():
    server = build_mcp_server(make_runtime())

    assert TOOL_MODULES == ()
    assert list_tools(server) == []


def test_server_publishes_instructions_file_name_and_version():
    server = build_mcp_server(make_runtime())

    async def action(client):
        return client.instructions, client.server_info

    instructions, server_info = with_client(server, action)

    assert instructions == INSTRUCTIONS_FILE.read_text(encoding="utf-8")
    assert server_info.name == "my-finance-mcp"
    assert server_info.version == importlib.metadata.version("my-finance-mcp")


def test_registered_tool_is_read_only_and_closed_world():
    [tool] = list_tools(sample_server())

    assert tool.name == TOOL_NAME
    wire = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert wire["annotations"]["readOnlyHint"] is True
    assert wire["annotations"]["openWorldHint"] is False


def test_registered_tool_output_schema_requires_meta_and_data():
    [tool] = list_tools(sample_server())

    assert "meta" in tool.output_schema["required"]
    assert "data" in tool.output_schema["required"]


def test_success_call_matches_output_schema_and_text_block():
    server = sample_server()
    [tool] = list_tools(server)

    result = call_sample(server)

    assert result.is_error is False
    jsonschema.validate(result.structured_content, tool.output_schema)
    assert result.structured_content["data"] == {
        "amount": 987654.32,
        "label": "total",
    }
    [block] = result.content
    assert block.type == "text"
    assert json.loads(block.text) == result.structured_content


def test_tool_input_error_call_returns_coded_text():
    result = call_sample(sample_server(input_error_handler))

    assert result.is_error is True
    assert result.structured_content is None
    [block] = result.content
    assert block.text.startswith("UNKNOWN_CATEGORY: ")
    assert block.text == (
        "UNKNOWN_CATEGORY: Categoria ZZMSG321 não encontrada. ZZHINT456"
    )


@pytest.mark.parametrize(
    ("handler", "identity"),
    [
        # The real identity, with no access token in the request context.
        (ok_handler, current_user),
        (unexpected_handler, fixed_identity),
    ],
    ids=["missing-identity", "unexpected-exception"],
)
def test_unexpected_failure_returns_generic_text_and_logs_traceback_once(
    caplog, handler, identity
):
    caplog.set_level(logging.DEBUG)

    result = call_sample(sample_server(handler, identity))

    assert result.is_error is True
    [block] = result.content
    assert block.text == f"Error executing tool {TOOL_NAME}"
    assert "ZZSECRET789" not in block.text
    tracebacks = [record for record in caplog.records if record.exc_info]
    assert len(tracebacks) == 1


@pytest.mark.parametrize("handler", [ok_handler, input_error_handler])
def test_client_calls_log_no_arguments_data_message_or_hint(caplog, handler):
    caplog.set_level(logging.DEBUG)

    call_sample(sample_server(handler))

    texts = [caplog.text]
    texts += [record.getMessage() for record in caplog.records]
    assert caplog.records
    assert [m for m in MARKERS if any(m in text for text in texts)] == []


def test_create_mcp_app_configures_stateless_json_transport():
    settings = Settings(_env_file=None, **SETTINGS_VALUES)
    server = build_mcp_server(make_runtime())

    app = create_mcp_app(server, settings)

    assert [route.path for route in app.routes] == ["/mcp"]
    manager = server.session_manager
    assert manager.stateless is True
    assert manager.json_response is True
    assert MAX_MCP_BODY_BYTES == 65536
    assert manager.max_request_body_size == 65536
    assert manager.security_settings == TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["finance-mcp.x.ts.net:*", "localhost:*"],
        allowed_origins=["https://claude.ai"],
    )
