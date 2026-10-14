from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class ToolResult:
    ok: bool
    output: str
    error: str | None = None
    event: dict | None = None   # optional UI event (e.g. a map) the engine forwards

class Tool(ABC):
    name: str
    description: str
    parameters: dict
    mutating: bool

    @abstractmethod
    async def run(self, args: dict) -> ToolResult: ...

class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(name)
        return self._tools[name]

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[dict]:
        return [{
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        } for t in self._tools.values()]
