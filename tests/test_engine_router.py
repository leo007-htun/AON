import pytest
from agent.engine import Agent
from agent.router import Plan
from agent.llm_client import ChatResult, ToolCall
from agent.tools.base import Tool, ToolResult, ToolRegistry
from agent.approval_gate import AutoGate


class Noop(Tool):
    name = "noop"; description = "noop"; parameters = {"type": "object", "properties": {}}; mutating = False
    def __init__(self): self.ran = 0
    async def run(self, args): self.ran += 1; return ToolResult(ok=True, output="ok")


class Mut(Tool):
    name = "mut"; description = "m"; parameters = {"type": "object", "properties": {}}; mutating = True
    def __init__(self): self.ran = 0
    async def run(self, args): self.ran += 1; return ToolResult(ok=True, output="x")


class LLM:
    def __init__(self, seq): self.seq = seq; self.i = 0; self.sys = None
    async def chat(self, m, tools=None):
        self.sys = m[0]["content"]; r = self.seq[min(self.i, len(self.seq) - 1)]; self.i += 1; return r


def _router(plan):
    async def r(goal, conversation=None): return plan
    return r


@pytest.mark.asyncio
async def test_clarify_stops_without_tools():
    t = Noop(); reg = ToolRegistry(); reg.register(t)
    llm = LLM([ChatResult("should not be called", [])])
    res = await Agent(llm, reg, AutoGate(),
                      router=_router(Plan("", "create_skill_from_url", [], True, "Which URL?", None))).run("go")
    assert res.status == "clarify" and res.final == "Which URL?" and t.ran == 0


@pytest.mark.asyncio
async def test_direct_answer_stops_without_tools():
    t = Noop(); reg = ToolRegistry(); reg.register(t)
    llm = LLM([ChatResult("unused", [])])
    res = await Agent(llm, reg, AutoGate(),
                      router=_router(Plan("", "answer", [], False, None, "Hello!"))).run("hi")
    assert res.status == "done" and res.final == "Hello!" and t.ran == 0


@pytest.mark.asyncio
async def test_plan_hint_reaches_llm_and_loop_runs():
    t = Noop(); reg = ToolRegistry(); reg.register(t)
    llm = LLM([ChatResult("", [ToolCall("1", "noop", {})]), ChatResult("done", [])])
    plan = Plan("do the noop", "shell_task", [{"tool": "noop", "args": {}}], False, None, None)
    res = await Agent(llm, reg, AutoGate(), router=_router(plan)).run("go")
    assert "noop()" in llm.sys and t.ran == 1 and res.status == "done"


@pytest.mark.asyncio
async def test_mutating_plan_still_gated():
    t = Mut(); reg = ToolRegistry(); reg.register(t)
    llm = LLM([ChatResult("", [ToolCall("1", "mut", {})]), ChatResult("done", [])])
    plan = Plan("do mut", "shell_task", [{"tool": "mut", "args": {}}], False, None, None)
    res = await Agent(llm, reg, AutoGate(approve=False), router=_router(plan)).run("go")
    assert t.ran == 0


@pytest.mark.asyncio
async def test_router_exception_falls_through():
    t = Noop(); reg = ToolRegistry(); reg.register(t)
    async def boom(goal, conversation=None): raise RuntimeError("down")
    llm = LLM([ChatResult("just answer", [])])
    res = await Agent(llm, reg, AutoGate(), router=boom).run("go")
    assert res.status == "done" and res.final == "just answer"


@pytest.mark.asyncio
async def test_no_router_unchanged():
    t = Noop(); reg = ToolRegistry(); reg.register(t)
    llm = LLM([ChatResult("hi", [])])
    res = await Agent(llm, reg, AutoGate()).run("go")
    assert res.status == "done" and res.final == "hi"
