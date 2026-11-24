"""Server capability tool: which optional Graylog APIs this server offers."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult

from ..application.capabilities import CapabilityService
from .responses import Responder
from .tool_annotations import READ_ONLY


def register(server: MCPServer, service: CapabilityService, respond: Responder) -> None:
    @server.tool(title="Server capabilities", annotations=READ_ONLY)
    def server_capabilities() -> CallToolResult:
        """Graylog version and which optional APIs it offers (server-side aggregation,
        time grouping, field types, query validation, paged streams), with the tools
        that return Unsupported on this server. Check it before relying on counting
        tools on an unfamiliar or older Graylog."""
        return respond(service.server_capabilities)
