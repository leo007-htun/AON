import pytest
from agent.engine import Agent, RunResult
from agent.llm_client import ChatResult, ToolCall
from agent.tools.base import Tool, ToolResult, ToolRegistry
from agent.approval_gate import AutoGate

class FakeLLM:
    def __init__(self, scripted): self.scripted = list(scripted); self.calls = 0
    async def chat(self, messages, tools=None):
        r = self.scripted[min(self.calls, len(self.scripted)-1)]; self.calls += 1
        return r

class Echo(Tool):
    name="read_file"; description=""; parameters={}; mutating=False
    async def run(self, args): return ToolResult(ok=True, output="file contents")

def _reg():
    r = ToolRegistry(); r.register(Echo()); return r

@pytest.mark.asyncio
async def test_run_completes_when_no_tool_calls():
    llm = FakeLLM([
        ChatResult(content="", tool_calls=[ToolCall("1","read_file",{"path":"a"})]),
        ChatResult(content="done: the file says hi", tool_calls=[]),
    ])
    res = await Agent(llm, _reg(), AutoGate(), max_steps=10).run("read a")
    assert isinstance(res, RunResult) and res.status == "done"
    assert "done:" in res.final and len(res.steps) == 1

@pytest.mark.asyncio
async def test_run_goes_stuck_at_budget():
    loop_call = ChatResult(content="", tool_calls=[ToolCall("1","read_file",{"path":"a"})])
    llm = FakeLLM([loop_call])
    res = await Agent(llm, _reg(), AutoGate(), max_steps=3).run("spin")
    assert res.status == "stuck" and len(res.steps) == 3
