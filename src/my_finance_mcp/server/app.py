"""MCP server factory, tool catalog and HTTP transport app."""

import importlib.metadata
from collections.abc import Callable, Sequence
from importlib import resources
from typing import Protocol, TypeVar

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from starlette.applications import Starlette

from my_finance_mcp.config import Settings
from my_finance_mcp.server.runtime import ToolRuntime

F = TypeVar("F", bound=Callable[..., object])

SERVER_NAME = "my-finance-mcp"
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
MAX_MCP_BODY_BYTES = 65536


class ToolModule(Protocol):
    def register(
        self, finance_tool: Callable[[F], F], runtime: ToolRuntime
    ) -> None: ...


TOOL_MODULES: tuple[ToolModule, ...] = ()


def load_instructions() -> str:
    guides = resources.files("my_finance_mcp.server.guides")
    return guides.joinpath("instructions.md").read_text(encoding="utf-8")


def build_mcp_server(
    runtime: ToolRuntime, tool_modules: Sequence[ToolModule] = TOOL_MODULES
) -> MCPServer:
    mcp = MCPServer(
        name=SERVER_NAME,
        version=importlib.metadata.version(SERVER_NAME),
        instructions=load_instructions(),
    )
    finance_tool = mcp.tool(annotations=READ_ONLY, structured_output=True)
    for module in tool_modules:
        module.register(finance_tool, runtime)
    return mcp


def create_mcp_app(mcp: MCPServer, settings: Settings) -> Starlette:
    return mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        max_request_body_size=MAX_MCP_BODY_BYTES,
        transport_security=TransportSecuritySettings(
            allowed_hosts=settings.MCP_ALLOWED_HOSTS,
            allowed_origins=settings.MCP_ALLOWED_ORIGINS,
        ),
    )
