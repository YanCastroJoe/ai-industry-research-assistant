from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from .execution import RetryableToolError


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    command: str
    args: list[str]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)


class MCPStdioToolAdapter:
    """Discover and call MCP tools over stdio, then expose them through ToolRegistry."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config

    def _parameters(self) -> StdioServerParameters:
        return StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            cwd=self.config.cwd,
            env=self.config.env or None,
        )

    async def _list_tools(self) -> list[dict[str, Any]]:
        async with stdio_client(self._parameters()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.list_tools()
                return [
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "input_schema": tool.inputSchema,
                    }
                    for tool in response.tools
                ]

    def list_tools(self) -> list[dict[str, Any]]:
        return asyncio.run(self._list_tools())

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        async with stdio_client(self._parameters()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments=arguments)
                if result.isError:
                    message = " ".join(block.text for block in result.content if isinstance(block, TextContent))
                    raise RetryableToolError(message or f"MCP tool {name} failed")
                if result.structuredContent is not None:
                    return result.structuredContent
                text = "\n".join(block.text for block in result.content if isinstance(block, TextContent))
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text

    def call_tool(self, name: str, **arguments: Any) -> Any:
        return asyncio.run(self._call_tool(name, arguments))

    def register_tools(self, registry: Any, aliases: dict[str, str] | None = None) -> list[str]:
        aliases = aliases or {}
        registered = []
        for tool in self.list_tools():
            remote_name = str(tool["name"])
            local_name = aliases.get(remote_name, f"mcp__{self.config.name}__{remote_name}")

            def handler(_remote_name: str = remote_name, **kwargs: Any) -> Any:
                output = self.call_tool(_remote_name, **kwargs)
                if _remote_name == "retrieve_project_evidence" and isinstance(output, dict):
                    return output.get("evidence", [])
                return output

            registry.register(
                local_name,
                str(tool["description"]),
                handler,
                dict(tool["input_schema"]),
                source=f"mcp:{self.config.name}",
            )
            registered.append(local_name)
        return registered
