"""Typed contracts and safe errors shared by the narrow MCP server modules."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

JsonRpcId = str | int
ProgressReporter = Callable[[float, str], Awaitable[None]]
ToolHandler = Callable[[Mapping[str, Any], ProgressReporter], Awaitable[dict[str, Any]]]
SendMessage = Callable[[dict[str, Any]], Awaitable[None]]


class McpToolError(RuntimeError):
    """A safe, user-facing tool failure that may be returned to an MCP caller."""


@dataclass(frozen=True)
class McpTool:
    """One explicitly registered MCP tool and its JSON Schema contract."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    annotations: dict[str, Any] | None = None

    def descriptor(self) -> dict[str, Any]:
        """Return the protocol-facing description, excluding Python handlers."""
        descriptor = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
        if self.annotations is not None:
            descriptor["annotations"] = self.annotations
        return descriptor
