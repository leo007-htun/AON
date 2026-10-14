import pytest
from agent.engine import Agent
from agent.llm_client import ChatResult, ToolCall
from agent.tools.base import Tool, ToolResult, ToolRegistry
from agent.approval_gate import AutoGate


class FakeMem:
    def __init__(self): self.added = []
    def read(self): return ""
    def add(self, content, type="fact", tags="", source="manual"):
        self.added.append({"content": content, "tags": tags}); return 1


class RC(Tool):
    name = "run_command"; description = "d"; parameters = {"type": "object", "properties": {}}; mutating = False
    async def run(self, args): return ToolResult(ok=True, output="ok")


class SeqLLM:
    def __init__(self, seq): self.seq = seq; self.i = 0
    async def chat(self, m, tools=None):
        r = self.seq[min(self.i, len(self.seq) - 1)]; self.i += 1; return r


@pytest.mark.asyncio
async def test_install_is_captured():
    mem = FakeMem(); reg = ToolRegistry(); reg.register(RC())
    llm = SeqLLM([ChatResult("", [ToolCall("1", "run_command", {"command": "pip install foo"})]),
                  ChatResult("done", [])])
    await Agent(llm, reg, AutoGate(), memory=mem).run("go")
    assert any("install" in a["tags"] and "pip install foo" in a["content"] for a in mem.added)


@pytest.mark.asyncio
async def test_non_install_not_captured():
    mem = FakeMem(); reg = ToolRegistry(); reg.register(RC())
    llm = SeqLLM([ChatResult("", [ToolCall("1", "run_command", {"command": "ls"})]),
                  ChatResult("done", [])])
    await Agent(llm, reg, AutoGate(), memory=mem).run("go")
    assert not any(a["tags"] == "install" for a in mem.added)
