"""Cluster health tools."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ..application.operations import OperationsService
from .responses import respond


def register(server: MCPServer, service: OperationsService) -> None:
    @server.tool()
    def input_status(only_problems: bool = False) -> str:
        """Each log input and whether it is running on every node. Failed or stopped
        inputs usually explain logs that stopped arriving.

        Args:
            only_problems: True to return only inputs that are not running everywhere.
        """
        return respond(lambda: service.input_status(only_problems=only_problems))

    @server.tool()
    def throughput() -> str:
        """Messages per second in and out, buffer usage and uncommitted journal entries,
        per node and in total. A growing journal means Graylog is falling behind."""
        return respond(service.throughput)

    @server.tool()
    def system_notifications() -> str:
        """Graylog's own warnings (e.g. journal full, index failures, node not
        processing), urgent first."""
        return respond(service.notifications)

    @server.tool()
    def cluster_nodes() -> str:
        """Every Graylog node with version, leader flag, lifecycle and processing
        state, and whether it is healthy."""
        return respond(service.cluster_nodes)
