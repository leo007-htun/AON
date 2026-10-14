from __future__ import annotations
from agent.tools.base import Tool, ToolResult


class McpTool(Tool):
    mutating = True

    def __init__(self, manager, spec):
        self.manager = manager
        self.spec = spec
        self.name = f"mcp__{spec.server}__{spec.name}"
        self.description = spec.description or f"MCP tool {spec.name} from {spec.server}"
        self.parameters = spec.input_schema or {"type": "object", "properties": {}}

    async def run(self, args):
        try:
            return ToolResult(ok=True, output=await self.manager.call_tool(self.spec.server, self.spec.name, args))
        except Exception as e:
            return ToolResult(ok=False, output="", error=str(e))
