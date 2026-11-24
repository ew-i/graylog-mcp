"""MCP tool annotations shared by every tool.

Every tool only reads from Graylog, so clients may run them without asking for
confirmation and retry them freely. Graylog is an external system whose data
keeps changing, hence open-world.
"""

from __future__ import annotations

from mcp.types import ToolAnnotations

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
