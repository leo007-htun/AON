from fastapi.testclient import TestClient
from agent.config import Config
from agent.persistence import Database
from agent.web import create_app
from agent.engine import RunResult


def _app(tmp, seen, db=None):
    db = db or Database(str(tmp / "a.db"))
    class FakeAgent:
        def __init__(self, on_event): self.on_event = on_event
        async def run(self, goal, conversation=None):
            seen.append(conversation)
            await self.on_event({"type": "final", "status": "done", "final": "ANS:" + goal})
            return RunResult("done", "ANS:" + goal, [])
    def make_agent(gate, on_event, model, convo="default"): return FakeAgent(on_event)
    return create_app(Config("http://x/v1", "m", tmp, [], 5, 10, "127.0.0.1:8700"), db, make_agent)


def test_second_turn_sees_first(tmp_path):
    seen = []
    c = TestClient(_app(tmp_path, seen))
    with c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "goal", "goal": "make a skill from a url", "model": "m", "session_id": "s1"})
        while ws.receive_json().get("type") != "final":
            pass
    with c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "goal", "goal": "https://x", "model": "m", "session_id": "s1"})
        while ws.receive_json().get("type") != "final":
            pass
    assert seen[0] == []
    joined = " ".join(t["content"] for t in seen[1])
    assert "make a skill from a url" in joined and "ANS:make a skill from a url" in joined


def test_store_failure_degrades(tmp_path):
    seen = []
    class BadDB(Database):
        def recent_turns(self, *a, **k): raise RuntimeError("down")
        def add_turn(self, *a, **k): raise RuntimeError("down")
    c = TestClient(_app(tmp_path, seen, db=BadDB(str(tmp_path / "b.db"))))
    with c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "goal", "goal": "go", "model": "m", "session_id": "s1"})
        final = None
        while True:
            m = ws.receive_json()
            if m.get("type") == "final":
                final = m; break
    assert final["final"] == "ANS:go" and seen[0] == []
