"""A failed model call ends the run with a readable error instead of a spinner that never stops,
and the tools that call the model themselves use the model picked in the UI."""
import httpx
from fastapi.testclient import TestClient
from agent.config import Config
from agent.persistence import Database
from agent.web import create_app, model_error_text
from agent.engine import Agent
from agent.build import build_registry
from agent.tools.base import ToolRegistry


def _cfg(tmp):
    return Config("http://x/v1", "gpt-oss", tmp, [], 5, 10, "127.0.0.1:8700")


def _status_error(code, body):
    req = httpx.Request("POST", "http://x/v1/chat/completions")
    return httpx.HTTPStatusError("bad", request=req, response=httpx.Response(code, json=body, request=req))


class RaisingLLM:
    def __init__(self, exc): self.exc = exc
    async def chat(self, m, tools=None): raise self.exc


def _app(tmp, exc):
    db = Database(str(tmp / "d.db"))
    def make_agent(gate, on_event, model, convo="default"):
        return Agent(RaisingLLM(exc), ToolRegistry(), gate, max_steps=3, on_event=on_event)
    return create_app(_cfg(tmp), db, make_agent, mem_path=tmp / "m.db"), db


def _final(ws):
    while True:
        m = ws.receive_json()
        if m["type"] == "final":
            return m


def test_rejected_model_call_ends_with_error(tmp_path):
    exc = _status_error(400, {"error": {"message": "registry.ollama.ai/library/AON:latest does not support tools"}})
    app, db = _app(tmp_path, exc)
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"type": "goal", "goal": "I love having a nice chat", "model": "AON:latest", "session_id": "c1"})
        m = _final(ws)
    assert m["status"] == "error"
    assert "does not support tools" in m["final"] and "AON:latest" in m["final"]


def test_error_reply_is_not_saved_as_an_assistant_turn(tmp_path):
    app, db = _app(tmp_path, _status_error(400, {"error": {"message": "boom"}}))
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"type": "goal", "goal": "hello", "model": "m", "session_id": "c1"})
        _final(ws)
    assert [t["role"] for t in db.recent_turns("c1", 10)] == ["user"]


def test_unreachable_model_ends_with_error(tmp_path):
    app, _ = _app(tmp_path, httpx.ConnectError("refused"))
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"type": "goal", "goal": "hello", "model": "m"})
        m = _final(ws)
    assert m["status"] == "error" and "could not reach" in m["final"].lower()


def test_context_overflow_message_says_what_to_do():
    exc = _status_error(400, {"error": {"message": "request (40000 tokens) exceeds the available context size (32768 tokens)"}})
    text = model_error_text(exc, "AON:latest")
    assert "exceeds the available context size" in text and "new chat" in text.lower()


def test_plain_text_error_body():
    req = httpx.Request("POST", "http://x")
    exc = httpx.HTTPStatusError("x", request=req, response=httpx.Response(500, text="llama runner crashed", request=req))
    assert "llama runner crashed" in model_error_text(exc, "m")


def test_model_calling_tools_use_the_chosen_model(tmp_path):
    reg = build_registry(_cfg(tmp_path), mem_path=tmp_path / "m.db", model="AON:latest")
    assert reg.get("learn_url").llm.model == "AON:latest"
    assert reg.get("draft_skill").llm.model == "AON:latest"


def test_model_calling_tools_default_to_config_model(tmp_path):
    reg = build_registry(_cfg(tmp_path), mem_path=tmp_path / "m.db")
    assert reg.get("learn_url").llm.model == "gpt-oss"
