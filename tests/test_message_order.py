import pytest
from aon.engine import Agent
from aon.prompts import build_messages
from aon.llm_client import ChatResult, ToolCall
from aon.tools.base import Tool, ToolResult, ToolRegistry
from aon.approval_gate import AutoGate

CALL = {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function",
        "function": {"name": "list_dir", "arguments": "{}"}}]}
RESULT = {"role": "tool", "tool_call_id": "1", "content": "a.txt"}

def test_goal_comes_before_this_runs_tool_steps():
    # standard tool-chat order: the request, then the calls/results answering it
    convo = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "earlier reply"}]
    msgs = build_messages("do it", convo, steps=[CALL, RESULT])
    roles = [m["role"] for m in msgs]
    assert roles == ["system", "user", "assistant", "user", "assistant", "tool"]
    assert msgs[3]["content"] == "do it" and msgs[-1] == RESULT

def test_images_stay_on_the_goal_message_when_steps_follow():
    img = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}
    msgs = build_messages("what is this", [], steps=[CALL, RESULT], images=[img])
    assert msgs[1]["role"] == "user" and img in msgs[1]["content"]
    assert msgs[-1] == RESULT

class Lister(Tool):
    name = "list_dir"; description = ""; parameters = {}; mutating = False
    async def run(self, args): return ToolResult(ok=True, output="a.txt")

class LLM:
    def __init__(self, seq): self.seq = seq; self.i = 0; self.seen = []
    async def chat(self, m, tools=None):
        self.seen.append(m); r = self.seq[min(self.i, len(self.seq) - 1)]; self.i += 1; return r

@pytest.mark.asyncio
async def test_agent_shows_tool_result_after_the_goal():
    reg = ToolRegistry(); reg.register(Lister())
    llm = LLM([ChatResult("", [ToolCall("1", "list_dir", {"path": "."})]), ChatResult("a.txt is there", [])])
    res = await Agent(llm, reg, AutoGate()).run("list files",
                                                 conversation=[{"role": "user", "content": "hi"},
                                                               {"role": "assistant", "content": "hello"}])
    assert res.final == "a.txt is there"
    second = llm.seen[1]
    goal_i = max(i for i, m in enumerate(second) if m["role"] == "user" and m["content"] == "list files")
    tool_i = next(i for i, m in enumerate(second) if m["role"] == "tool")
    assert goal_i < tool_i and second[-1]["role"] == "tool"

class Boom(Tool):
    name = "list_dir"; description = ""; parameters = {}; mutating = False
    async def run(self, args): return ToolResult(ok=True, output=args["path"])  # KeyError without path

@pytest.mark.asyncio
async def test_tool_exception_becomes_error_step_not_a_crash():
    reg = ToolRegistry(); reg.register(Boom())
    llm = LLM([ChatResult("", [ToolCall("1", "list_dir", {})]), ChatResult("recovered", [])])
    res = await Agent(llm, reg, AutoGate()).run("list files")
    assert res.status == "done" and res.final == "recovered"
    assert res.steps[0].output.startswith("error:") and "path" in res.steps[0].output
