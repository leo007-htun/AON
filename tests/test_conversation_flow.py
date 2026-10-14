import pytest
from agent.engine import Agent
from agent.router import Plan, deliberate
from agent.llm_client import ChatResult
from agent.tools.base import ToolRegistry
from agent.approval_gate import AutoGate


class CapLLM:
    def __init__(self, out="done"): self.out = out; self.msgs = None
    async def chat(self, m, tools=None):
        self.msgs = m; return ChatResult(self.out, [])


@pytest.mark.asyncio
async def test_deliberate_includes_conversation():
    llm = CapLLM('{"understanding":"x","intent":"answer","plan":[],'
                 '"needs_clarification":false,"direct_answer":"hi"}')
    await deliberate("https://x", llm, conversation=[{"role": "assistant", "content": "Which URL?"}])
    user_msg = llm.msgs[1]["content"]
    assert "Which URL?" in user_msg and "Recent conversation" in user_msg


@pytest.mark.asyncio
async def test_agent_seeds_history_from_conversation():
    llm = CapLLM("answer")
    reg = ToolRegistry()
    convo = [{"role": "user", "content": "make a skill from a url"},
             {"role": "assistant", "content": "Which URL?"}]
    got = {}
    async def router(goal, conversation=None): got["c"] = conversation; return None
    res = await Agent(llm, reg, AutoGate(), router=router).run("https://x", conversation=convo)
    contents = [m["content"] for m in llm.msgs]
    assert "make a skill from a url" in contents and "Which URL?" in contents
    assert got["c"] == convo and res.final == "answer"


@pytest.mark.asyncio
async def test_agent_no_conversation_unchanged():
    llm = CapLLM("hi")
    res = await Agent(llm, ToolRegistry(), AutoGate()).run("go")
    assert res.final == "hi"
