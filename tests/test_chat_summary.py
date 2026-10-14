"""A rolling summary per chat, kept in the database, covers turns older than the recent ones."""
import pytest
from agent.persistence import Database
from agent.llm_client import ChatResult
from agent.summary import update_summary, summary_background
from agent.context import estimate_tokens


class LLM:
    def __init__(self, reply="- user runs AON on port 8700"):
        self.reply = reply; self.calls = []
    async def chat(self, messages, tools=None):
        self.calls.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return ChatResult(self.reply, [])


def _db(tmp_path, n, size=40):
    db = Database(str(tmp_path / "d.db"))
    ids = [db.add_turn("c", "user" if i % 2 == 0 else "assistant", f"turn{i} " + "w" * size) for i in range(n)]
    return db, ids


def _prompt(call):
    return "\n".join(m["content"] for m in call)


@pytest.mark.asyncio
async def test_short_chat_gets_no_summary(tmp_path):
    db, _ = _db(tmp_path, 12)
    llm = LLM()
    assert await update_summary(db, llm, "c", keep=10, min_new=6) is False
    assert llm.calls == [] and db.get_summary("c") is None


@pytest.mark.asyncio
async def test_older_turns_are_summarised_once(tmp_path):
    db, ids = _db(tmp_path, 20)
    llm = LLM()
    assert await update_summary(db, llm, "c", keep=10, min_new=6) is True
    s = db.get_summary("c")
    assert s["text"] == "- user runs AON on port 8700" and s["upto_id"] == ids[9]
    p = _prompt(llm.calls[0])
    assert "turn0 " in p and "turn9 " in p and "turn10 " not in p      # recent turns are not folded in
    assert await update_summary(db, llm, "c", keep=10, min_new=6) is False
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_update_merges_into_the_previous_summary(tmp_path):
    db, ids = _db(tmp_path, 20)
    db.set_summary("c", "- earlier: user likes Rust", ids[3])
    llm = LLM()
    assert await update_summary(db, llm, "c", keep=10, min_new=6) is True
    p = _prompt(llm.calls[0])
    assert "user likes Rust" in p and "turn3 " not in p and "turn4 " in p


@pytest.mark.asyncio
async def test_model_failure_stores_nothing(tmp_path):
    db, _ = _db(tmp_path, 20)
    assert await update_summary(db, LLM(RuntimeError("down")), "c", keep=10, min_new=6) is False
    assert db.get_summary("c") is None


@pytest.mark.asyncio
async def test_empty_reply_stores_nothing(tmp_path):
    db, _ = _db(tmp_path, 20)
    assert await update_summary(db, LLM("   "), "c", keep=10, min_new=6) is False
    assert db.get_summary("c") is None


@pytest.mark.asyncio
async def test_thinking_is_stripped_and_length_capped(tmp_path):
    db, _ = _db(tmp_path, 20)
    await update_summary(db, LLM("<think>hmm</think>\n- fact " + "z" * 20000), "c", keep=10, min_new=6)
    t = db.get_summary("c")["text"]
    assert "hmm" not in t and t.startswith("- fact") and estimate_tokens(t) <= 800


@pytest.mark.asyncio
async def test_long_backlog_is_summarised_in_chunks(tmp_path):
    db, ids = _db(tmp_path, 70, size=3000)
    llm = LLM()
    assert await update_summary(db, llm, "c", keep=10, min_new=6) is True
    assert len(llm.calls) >= 2
    for call in llm.calls:
        assert sum(estimate_tokens(m["content"]) for m in call) < 12000
    assert db.get_summary("c")["upto_id"] == ids[59]


def test_summaries_are_per_chat(tmp_path):
    db = Database(str(tmp_path / "d.db"))
    db.set_summary("a", "A", 1); db.set_summary("b", "B", 2)
    assert db.get_summary("a")["text"] == "A" and db.get_summary("b")["upto_id"] == 2


def test_clearing_a_chat_clears_its_summary(tmp_path):
    db = Database(str(tmp_path / "d.db"))
    db.add_turn("a", "user", "x"); db.set_summary("a", "A", 1)
    db.clear_conversation("a")
    assert db.get_summary("a") is None


def test_background_text(tmp_path):
    db = Database(str(tmp_path / "d.db"))
    assert summary_background(db, "c") == ""
    db.set_summary("c", "- user runs AON", 5)
    bg = summary_background(db, "c")
    assert "- user runs AON" in bg and "earlier" in bg.lower()


def _web(tmp_path, monkeypatch, seen):
    from fastapi.testclient import TestClient
    from agent.config import Config
    from agent.web import create_app
    import agent.web as W
    db = Database(str(tmp_path / "d.db"))

    class FakeAgent:
        def __init__(self, on_event): self.on_event = on_event
        async def run(self, task, conversation=None, **kw):
            seen["kw"] = kw
            await self.on_event({"type": "final", "status": "done", "final": "ok"})
            return type("R", (), {"status": "done", "final": "ok"})()

    async def fake_update(db_, llm, convo, **k):
        seen["updated"] = (convo, llm.model)
        return True
    monkeypatch.setattr(W, "update_summary", fake_update)
    cfg = Config("http://x/v1", "m", tmp_path, [], 5, 10, "127.0.0.1:8700")
    app = create_app(cfg, db, lambda gate, on_event, model, convo="default": FakeAgent(on_event),
                     mem_path=tmp_path / "m.db")
    return TestClient(app), db


def _send(client, goal, wait_for=None):
    import time
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "goal", "goal": goal, "model": "AON:latest", "session_id": "c"})
        while True:
            m = ws.receive_json()
            if m["type"] == "final":
                break
        # the after-reply work runs on the server after the final event
        end = time.time() + 5
        while wait_for is not None and not wait_for() and time.time() < end:
            time.sleep(0.02)


def test_web_sends_the_summary_as_background(tmp_path, monkeypatch):
    seen = {}
    client, db = _web(tmp_path, monkeypatch, seen)
    db.set_summary("c", "- user runs AON on port 8700", 1)
    _send(client, "which port?")
    assert "port 8700" in seen["kw"]["background"]


def test_web_without_summary_sends_no_background(tmp_path, monkeypatch):
    seen = {}
    client, _ = _web(tmp_path, monkeypatch, seen)
    _send(client, "hi")
    assert "background" not in seen["kw"]


def test_web_updates_the_summary_after_the_reply_with_the_chat_model(tmp_path, monkeypatch):
    seen = {}
    client, _ = _web(tmp_path, monkeypatch, seen)
    _send(client, "hi", wait_for=lambda: "updated" in seen)
    assert seen["updated"] == ("c", "AON:latest")
