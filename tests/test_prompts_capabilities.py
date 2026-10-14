import pytest
from aon.prompts import build_messages
from aon.engine import Agent
from aon.llm_client import ChatResult
from aon.tools.base import Tool, ToolResult, ToolRegistry
from aon.approval_gate import AutoGate


def test_tool_summary_injected():
    msgs = build_messages("g", [], tool_summary="- run_command: run a shell command")
    sys = msgs[0]["content"]
    assert "capabilities" in sys.lower() and "run_command" in sys and "cannot" in sys.lower()


class Probe(Tool):
    name = "run_command"
    description = "run a shell command\n(second line)"
    parameters = {"type": "object", "properties": {}}
    mutating = False
    async def run(self, args):
        return ToolResult(ok=True, output="")


@pytest.mark.asyncio
async def test_agent_passes_tool_summary():
    cap = {}
    class LLM:
        async def chat(self, m, tools=None):
            cap["s"] = m[0]["content"]
            return ChatResult("done", [])
    reg = ToolRegistry()
    reg.register(Probe())
    await Agent(LLM(), reg, AutoGate()).run("go")
    assert "run_command: run a shell command" in cap["s"] and "(second line)" not in cap["s"]
