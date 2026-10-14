import pytest
from aon.engine import Agent
from aon.llm_client import ChatResult, ToolCall
from aon.tools.base import Tool, ToolResult, ToolRegistry
from aon.approval_gate import AutoGate

class Fail(Tool):
    name="read_file"; description=""; parameters={}; mutating=False
    async def run(self, args): return ToolResult(ok=False, output="", error="nope")

class FakeLLM:
    def __init__(self, r): self.r = r
    async def chat(self, messages, tools=None): return self.r

@pytest.mark.asyncio
async def test_repeated_identical_call_stops():
    reg = ToolRegistry(); reg.register(Fail())
    same = ChatResult(content="", tool_calls=[ToolCall("1","read_file",{"path":"a"})])
    res = await Agent(FakeLLM(same), reg, AutoGate(), max_steps=20, repeat_limit=3).run("loop")
    assert res.status == "stuck" and "repeat" in res.final.lower()
    assert len(res.steps) == 3
