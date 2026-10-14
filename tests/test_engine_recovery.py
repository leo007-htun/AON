import pytest
from aon.engine import Agent
from aon.llm_client import ChatResult
from aon.tools.base import Tool, ToolResult, ToolRegistry
from aon.approval_gate import AutoGate


class RecTool(Tool):
    name = "read_skill"
    description = "d"
    parameters = {"type": "object", "properties": {}}
    mutating = False

    def __init__(self):
        self.calls = []

    async def run(self, args):
        self.calls.append(args)
        return ToolResult(ok=True, output="ran")


class SeqLLM:
    def __init__(self, results):
        self.results = results
        self.i = 0

    async def chat(self, messages, tools=None):
        r = self.results[self.i]
        self.i += 1
        return r


def _reg():
    r = ToolRegistry()
    t = RecTool()
    r.register(t)
    return r, t


@pytest.mark.asyncio
async def test_recovers_content_embedded_call_command_key():
    reg, t = _reg()
    llm = SeqLLM([
        ChatResult(content='{"command":"read_skill","arguments":{"name":"x"}}', tool_calls=[]),
        ChatResult(content="done", tool_calls=[]),
    ])
    res = await Agent(llm, reg, AutoGate()).run("go")
    assert t.calls == [{"name": "x"}] and res.status == "done" and res.final == "done"


@pytest.mark.asyncio
async def test_recovers_fenced_json():
    reg, t = _reg()
    fenced = 'sure\n```json\n{"name":"read_skill","arguments":{"name":"y"}}\n```'
    llm = SeqLLM([
        ChatResult(content=fenced, tool_calls=[]),
        ChatResult(content="ok", tool_calls=[]),
    ])
    res = await Agent(llm, reg, AutoGate()).run("go")
    assert t.calls == [{"name": "y"}]


@pytest.mark.asyncio
async def test_unknown_tool_json_is_final_not_recovered():
    reg, t = _reg()
    llm = SeqLLM([ChatResult(content='{"name":"emailing","content":"x"}', tool_calls=[])])
    res = await Agent(llm, reg, AutoGate()).run("go")
    assert t.calls == [] and res.status == "done"


@pytest.mark.asyncio
async def test_prose_not_misread_as_call():
    reg, t = _reg()
    llm = SeqLLM([ChatResult(content="I will use read_skill next.", tool_calls=[])])
    res = await Agent(llm, reg, AutoGate()).run("go")
    assert t.calls == [] and res.final == "I will use read_skill next."
