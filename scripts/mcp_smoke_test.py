"""Run the local evidence retrieval step through the real MCP stdio transport."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.docflow import AgentRuntime
from app.mcp_adapter import MCPServerConfig, MCPStdioToolAdapter


def main() -> int:
    runtime = AgentRuntime()
    adapter = MCPStdioToolAdapter(
        MCPServerConfig(
            name="docflow",
            command=sys.executable,
            args=["-m", "app.mcp_server"],
            cwd=str(ROOT),
        )
    )
    registered = adapter.register_tools(runtime.registry, aliases={"retrieve_project_evidence": "retrieve_documents"})
    source = "项目本周完成需求澄清。测试环境尚未开放，可能影响联调排期。"
    result = runtime.execute("生成项目周报和风险清单", source)
    print(f"registered={registered}")
    print(f"retrieval_source={next(tool['source'] for tool in runtime.registry.describe() if tool['name'] == 'retrieve_documents')}")
    print(f"verified={result['verification']['passed']} evidence={len(result['evidence'])}")
    return 0 if result["verification"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
