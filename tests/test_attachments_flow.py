import base64, pytest
from fastapi.testclient import TestClient
from agent.config import Config
from agent.persistence import Database
from agent.web import create_app
from agent.engine import Agent, RunResult
from agent.prompts import build_messages
from agent.router import Plan
from agent.llm_client import ChatResult
from agent.tools.base import ToolRegistry
from agent.approval_gate import AutoGate

IMG = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

def test_build_messages_with_images_uses_content_parts():
    msgs = build_messages("what is this?", history=[], images=[IMG])
    last = msgs[-1]
    assert last["role"] == "user"
    assert last["content"] == [{"type": "text", "text": "what is this?"}, IMG]

def test_build_messages_without_images_unchanged():
    assert build_messages("hi", history=[])[-1] == {"role": "user", "content": "hi"}

class LLM:
    def __init__(self): self.msgs = None
    async def chat(self, m, tools=None): self.msgs = m; return ChatResult("a red square", [])

@pytest.mark.asyncio
async def test_agent_sends_images_to_model():
    llm = LLM()
    res = await Agent(llm, ToolRegistry(), AutoGate()).run("describe", images=[IMG])
    assert res.final == "a red square"
    assert IMG in llm.msgs[-1]["content"]

@pytest.mark.asyncio
async def test_router_skipped_when_images_attached():
    # the planner cannot see images; a direct planner answer would be made up
    called = []
    async def router(goal, conversation=None):
        called.append(goal); return Plan("", "answer", [], False, None, "made-up answer")
    llm = LLM()
    res = await Agent(llm, ToolRegistry(), AutoGate(), router=router).run("describe", images=[IMG])
    assert called == [] and res.final == "a red square"

@pytest.mark.asyncio
async def test_router_still_used_without_images():
    async def router(goal, conversation=None): return Plan("", "answer", [], False, None, "planner answer")
    res = await Agent(LLM(), ToolRegistry(), AutoGate(), router=router).run("hi")
    assert res.final == "planner answer"

# ---- websocket ----

class FakeAgent:
    def __init__(self, seen, emit): self.seen = seen; self.emit = emit
    async def run(self, goal, conversation=None, images=None):
        self.seen["goal"] = goal; self.seen["images"] = images
        await self.emit({"type": "final", "status": "done", "final": "ok"})
        return RunResult("done", "ok", [])

def _app(tmp, seen, vision=True):
    cfg = Config("http://x/v1", "m", tmp, [], 5, 10, "127.0.0.1:8700")
    db = Database(str(tmp / "d.db"))
    async def vcheck(base_url, model): seen["vision_model"] = model; return vision
    app = create_app(cfg, db, lambda gate, emit, model, convo="default": FakeAgent(seen, emit),
                     vision_check=vcheck)
    return app, db

def _att(name, data, type_=""):
    return {"name": name, "type": type_, "data": base64.b64encode(data).decode()}

def _run(app, payload):
    msgs = []
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json(payload)
        while True:
            m = ws.receive_json(); msgs.append(m)
            if m["type"] == "final": break
    return msgs

def test_ws_saves_files_and_tells_agent(tmp_path):
    seen = {}
    app, db = _app(tmp_path, seen)
    _run(app, {"type": "goal", "goal": "summarise this", "model": "m", "session_id": "s1",
               "attachments": [_att("notes.txt", b"banana report", "text/plain")]})
    saved = tmp_path / "uploads"
    files = list(saved.iterdir())
    assert len(files) == 1 and files[0].read_bytes() == b"banana report"
    assert seen["goal"].startswith("summarise this")
    assert str(files[0]) in seen["goal"] and "banana report" in seen["goal"]
    assert not seen["images"]
    # the saved turn keeps the note so later turns know where the file is
    turns = db.recent_turns("s1", 5)
    assert str(files[0]) in turns[0]["content"]

def test_ws_images_go_to_vision_model(tmp_path):
    seen = {}
    app, _ = _app(tmp_path, seen, vision=True)
    _run(app, {"type": "goal", "goal": "what is this", "model": "vis",
               "attachments": [_att("p.png", PNG, "image/png")]})
    assert seen["vision_model"] == "vis"
    assert len(seen["images"]) == 1 and seen["images"][0]["type"] == "image_url"

def test_ws_images_not_sent_to_blind_model(tmp_path):
    seen = {}
    app, _ = _app(tmp_path, seen, vision=False)
    msgs = _run(app, {"type": "goal", "goal": "what is this", "model": "blind",
                      "attachments": [_att("p.png", PNG, "image/png")]})
    assert not seen["images"]
    assert "cannot see" in seen["goal"]
    notes = [m for m in msgs if m["type"] == "notice"]
    assert notes and "blind" in notes[0]["text"]

def test_ws_bad_attachment_reports_error_without_running(tmp_path):
    seen = {}
    app, _ = _app(tmp_path, seen)
    msgs = _run(app, {"type": "goal", "goal": "x", "model": "m",
                      "attachments": [{"name": "a.txt", "type": "", "data": "%%%"}]})
    assert "goal" not in seen
    assert msgs[-1]["type"] == "final" and msgs[-1]["status"] == "error"
    assert "attachment" in msgs[-1]["final"].lower()

def test_ws_without_attachments_unchanged(tmp_path):
    seen = {}
    app, _ = _app(tmp_path, seen)
    _run(app, {"type": "goal", "goal": "plain goal", "model": "m"})
    assert seen["goal"] == "plain goal" and not seen["images"]
    assert "vision_model" not in seen
