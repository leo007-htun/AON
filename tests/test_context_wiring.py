"""The agent keeps each request inside its context limit and passes chat background along."""
import pytest
from pathlib import Path
from aon.config import Config, load_config
from aon.engine import Agent
from aon.approval_gate import AutoGate
from aon.llm_client import ChatResult, ToolCall
from aon.tools.base import Tool, ToolResult, ToolRegistry
from aon.context import message_tokens, estimate_tokens


class BigRead(Tool):
    name = "read_file"; description = "read"; parameters = {"type": "object", "properties": {}}; mutating = False
    async def run(self, args): return ToolResult(ok=True, output="B" * 200000)


class Recorder:
    def __init__(self, replies): self.replies = list(replies); self.calls = []
    async def chat(self, messages, tools=None):
        self.calls.append((messages, tools))
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


def _size(messages, tools):
    return sum(message_tokens(m) for m in messages) + estimate_tokens(tools or [])


@pytest.mark.asyncio
async def test_requests_stay_under_the_context_limit():
    reg = ToolRegistry(); reg.register(BigRead())
    llm = Recorder([ChatResult("", [ToolCall("1", "read_file", {})]), ChatResult("done", [])])
    agent = Agent(llm, reg, AutoGate(), max_steps=3, context_limit=12000)
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "h" * 20000} for i in range(10)]
    res = await agent.run("summarise the file", conversation=history)
    assert res.status == "done"
    for messages, tools in llm.calls:
        assert _size(messages, tools) <= 12000
    assert llm.calls[1][0][-1]["role"] == "tool"          # the result is still there, shortened


@pytest.mark.asyncio
async def test_no_limit_sends_everything():
    llm = Recorder([ChatResult("ok", [])])
    agent = Agent(llm, ToolRegistry(), AutoGate(), max_steps=2)
    await agent.run("g", conversation=[{"role": "user", "content": "x" * 90000}])
    assert llm.calls[0][0][1]["content"] == "x" * 90000


@pytest.mark.asyncio
async def test_background_reaches_the_system_prompt():
    llm = Recorder([ChatResult("ok", [])])
    agent = Agent(llm, ToolRegistry(), AutoGate(), max_steps=2)
    await agent.run("what did we decide?", background="Summary: we chose port 8123.")
    assert "we chose port 8123" in llm.calls[0][0][0]["content"]


@pytest.mark.asyncio
async def test_background_reaches_the_planner():
    seen = {}
    async def router(goal, conversation=None):
        seen["c"] = conversation
        return None
    agent = Agent(Recorder([ChatResult("ok", [])]), ToolRegistry(), AutoGate(), max_steps=2, router=router)
    await agent.run("what did we decide?", conversation=[{"role": "user", "content": "hi"}],
                    background="Summary: we chose port 8123.")
    assert "we chose port 8123" in seen["c"][0]["content"]
    assert seen["c"][-1] == {"role": "user", "content": "hi"}


def test_config_context_defaults(tmp_path):
    c = Config("http://x/v1", "m", tmp_path, [], 5, 10, "127.0.0.1:8700")
    assert (c.context_window, c.reply_reserve, c.history_tokens) == (32768, 6144, 8000)


def test_config_reads_context_settings(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('ollama_base_url="http://x/v1"\ndefault_model="m"\nworkdir="."\n'
                 'context_window=20480\nreply_reserve=4096\nhistory_tokens=6000\nembed_model="e"\n')
    c = load_config(p)
    assert (c.context_window, c.reply_reserve, c.history_tokens, c.embed_model) == (20480, 4096, 6000, "e")


def test_web_sends_only_the_history_that_fits(tmp_path):
    from fastapi.testclient import TestClient
    from aon.persistence import Database
    from aon.web import create_app
    db = Database(str(tmp_path / "d.db"))
    for i in range(20):
        db.add_turn("c1", "user" if i % 2 == 0 else "assistant", f"t{i} " + "q" * 9000)
    seen = {}

    class FakeAgent:
        def __init__(self, on_event): self.on_event = on_event
        async def run(self, task, conversation=None, **kw):
            seen["conv"] = conversation
            await self.on_event({"type": "final", "status": "done", "final": "ok"})
            return type("R", (), {"status": "done", "final": "ok"})()

    cfg = Config("http://x/v1", "m", tmp_path, [], 5, 10, "127.0.0.1:8700", history_tokens=7000)
    app = create_app(cfg, db, lambda gate, on_event, model, convo="default": FakeAgent(on_event),
                     mem_path=tmp_path / "m.db")
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"type": "goal", "goal": "next", "model": "m", "session_id": "c1"})
        while ws.receive_json()["type"] != "final":
            pass
    conv = seen["conv"]
    assert sum(message_tokens(t) for t in conv) <= 7000
    assert conv[-1]["content"].startswith("t19")
