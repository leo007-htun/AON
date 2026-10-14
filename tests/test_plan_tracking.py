import pytest
from aon.router import render_step, plan_progress, Plan
from aon.engine import Agent
from aon.llm_client import ChatResult, ToolCall
from aon.tools.base import Tool, ToolResult, ToolRegistry
from aon.approval_gate import AutoGate


def test_render_step_and_progress():
    s = render_step({"tool": "run_command", "args": {"command": "which node"}})
    assert s == "run_command(command=which node)"
    block = plan_progress(["a", "b", "c"], done=1, last_error="boom", understanding="u")
    assert "[x] a" in block and "[>] b" in block and "[ ] c" in block
    assert "u" in block and "boom" in block


class Tcmd(Tool):
    name = "run_command"; description = "d"; parameters = {"type": "object", "properties": {}}; mutating = False
    def __init__(self, fail_on=None): self.fail_on = fail_on; self.calls = []
    async def run(self, args):
        self.calls.append(args)
        if self.fail_on and args.get("command") == self.fail_on:
            return ToolResult(ok=False, output="", error="nope")
        return ToolResult(ok=True, output="ok")


class SeqLLM:
    def __init__(self, seq): self.seq = seq; self.i = 0; self.syslog = []
    async def chat(self, m, tools=None):
        self.syslog.append(m[0]["content"]); r = self.seq[min(self.i, len(self.seq) - 1)]; self.i += 1; return r


def _router(plan):
    async def r(goal, conversation=None): return plan
    return r


@pytest.mark.asyncio
async def test_progress_advances():
    t = Tcmd(); reg = ToolRegistry(); reg.register(t)
    llm = SeqLLM([ChatResult("", [ToolCall("1", "run_command", {"command": "which node"})]),
                  ChatResult("", [ToolCall("2", "run_command", {"command": "npm i x"})]),
                  ChatResult("done", [])])
    plan = Plan("set up", "shell_task",
                [{"tool": "run_command", "args": {"command": "which node"}},
                 {"tool": "run_command", "args": {"command": "npm i x"}}], False, None, None)
    await Agent(llm, reg, AutoGate(), router=_router(plan)).run("go")
    assert "[>] run_command(command=which node)" in llm.syslog[0]
    assert "[x] run_command(command=which node)" in llm.syslog[1]


@pytest.mark.asyncio
async def test_failed_step_notes_and_holds():
    t = Tcmd(fail_on="npm i x"); reg = ToolRegistry(); reg.register(t)
    llm = SeqLLM([ChatResult("", [ToolCall("1", "run_command", {"command": "npm i x"})]),
                  ChatResult("done", [])])
    plan = Plan("s", "shell_task", [{"tool": "run_command", "args": {"command": "npm i x"}}], False, None, None)
    await Agent(llm, reg, AutoGate(), router=_router(plan)).run("go")
    assert "last step errored" in llm.syslog[1] and "[x]" not in llm.syslog[1]
