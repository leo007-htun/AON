"""Recall across all chats: relevant turns from other chats come back, labelled with their date;
deleting a chat in the UI deletes it on the server so it never comes back."""
import time
import pytest
from fastapi.testclient import TestClient
from aon.config import Config
from aon.persistence import Database
from aon.web import create_app
from aon.recall import index_all, index_turns, recall, recall_other_chats, other_chats_background
from aon.memory_extract import SearchConversationTool
from tests.test_recall import FakeEmbedder


def _db(tmp_path):
    return Database(str(tmp_path / "d.db"))


def _seed(db):
    db.add_turn("old", "user", "I set the docker port to 8123 for the server")
    db.add_turn("old", "assistant", "Done")
    db.add_turn("food", "user", "my favourite food is pizza with extra cheese")
    db.add_turn("now", "user", "hello there friend")


@pytest.mark.asyncio
async def test_index_all_covers_every_chat_within_a_limit(tmp_path):
    db = _db(tmp_path); _seed(db)
    e = FakeEmbedder()
    assert await index_all(db, e, limit=2) == 2
    assert await index_all(db, e, limit=10) == 2
    assert await index_all(db, e) == 0


@pytest.mark.asyncio
async def test_finds_the_turn_in_another_chat_with_its_answer(tmp_path):
    db = _db(tmp_path); _seed(db)
    e = FakeEmbedder(); await index_all(db, e)
    hits = await recall_other_chats(db, e, "now", "which docker port did I pick?")
    assert [h["content"] for h in hits] == ["I set the docker port to 8123 for the server", "Done"]
    assert all(h["convo"] == "old" for h in hits)


@pytest.mark.asyncio
async def test_the_current_chat_is_left_to_in_chat_recall(tmp_path):
    db = _db(tmp_path); _seed(db)
    e = FakeEmbedder(); await index_all(db, e)
    assert await recall_other_chats(db, e, "old", "docker port") == []


@pytest.mark.asyncio
async def test_unrelated_questions_bring_nothing(tmp_path):
    db = _db(tmp_path); _seed(db)
    e = FakeEmbedder(); await index_all(db, e)
    assert await other_chats_background(db, e, "now", "tell me about rust") == ""


@pytest.mark.asyncio
async def test_background_is_labelled_with_the_date(tmp_path):
    db = _db(tmp_path); _seed(db)
    e = FakeEmbedder(); await index_all(db, e)
    bg = await other_chats_background(db, e, "now", "docker port?")
    assert "other chats" in bg.lower() and "8123" in bg and time.strftime("%Y-%m-%d") in bg


@pytest.mark.asyncio
async def test_error_turns_are_never_recalled(tmp_path):
    db = _db(tmp_path)
    db.add_turn("old", "assistant", "Stopped: repeated the same call 3x without progress: docker port docker")
    db.add_turn("old", "assistant", "The model returned an empty reply (no text and no tool call). docker port")
    db.add_turn("c", "user", "(stopped by the user) docker port docker port")
    e = FakeEmbedder(); await index_all(db, e)
    assert await recall_other_chats(db, e, "now", "docker port") == []
    await index_turns(db, e, "c")
    assert await recall(db, e, "c", "docker port", exclude_ids=set()) == []


@pytest.mark.asyncio
async def test_search_conversation_can_search_all_chats(tmp_path):
    db = _db(tmp_path); _seed(db)
    e = FakeEmbedder()
    tool = SearchConversationTool(db, "now", embedder=e)
    assert "8123" not in (await tool.run({"query": "docker port"})).output
    assert "8123" in (await tool.run({"query": "docker port", "all_chats": True})).output


def test_config_cross_chat_default_and_toml(tmp_path):
    from aon.config import load_config
    assert Config("http://x/v1", "m", tmp_path, [], 5, 10, "127.0.0.1:8700").cross_chat_recall is True
    p = tmp_path / "c.toml"
    p.write_text('ollama_base_url="http://x/v1"\ndefault_model="m"\nworkdir="."\ncross_chat_recall=false\n')
    assert load_config(p).cross_chat_recall is False


class _Agent:
    def __init__(self, on_event, seen): self.on_event = on_event; self.seen = seen
    async def run(self, task, conversation=None, **kw):
        self.seen["kw"] = kw
        await self.on_event({"type": "final", "status": "done", "final": "ok"})
        return type("R", (), {"status": "done", "final": "ok"})()


def _app(tmp_path, seen, **cfgkw):
    db = _db(tmp_path); _seed(db)
    cfg = Config("http://x/v1", "m", tmp_path, [], 5, 10, "127.0.0.1:8700", **cfgkw)
    app = create_app(cfg, db, lambda gate, on_event, model, convo="default": _Agent(on_event, seen),
                     mem_path=tmp_path / "m.db", embedder=FakeEmbedder())
    return TestClient(app), db


def _ask(client, goal, convo="now"):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "goal", "goal": goal, "model": "m", "session_id": convo})
        while ws.receive_json()["type"] != "final":
            pass


def test_web_brings_relevant_turns_from_other_chats(tmp_path):
    seen = {}
    client, _ = _app(tmp_path, seen)
    _ask(client, "which docker port did I pick?")
    assert "8123" in seen["kw"]["background"]


def test_web_cross_chat_can_be_switched_off(tmp_path):
    seen = {}
    client, _ = _app(tmp_path, seen, cross_chat_recall=False)
    _ask(client, "which docker port did I pick?")
    assert "8123" not in seen["kw"].get("background", "")


def test_deleting_a_chat_removes_it_from_the_server(tmp_path):
    seen = {}
    client, db = _app(tmp_path, seen)
    assert client.delete("/api/conversations/old").json() == {"ok": True}
    assert db.recent_turns("old", 10) == []
    _ask(client, "which docker port did I pick?")
    assert "8123" not in seen["kw"].get("background", "")


def test_the_page_deletes_chats_on_the_server():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "src" / "aon" / "static" / "index.html").read_text(encoding="utf-8")
    assert "fetch('/api/conversations/'+encodeURIComponent(id),{method:'DELETE'})" in html


@pytest.mark.asyncio
async def test_backfill_indexes_every_turn(tmp_path):
    from aon.recall import backfill
    db = _db(tmp_path)
    for i in range(300):
        db.add_turn(f"c{i % 7}", "user", f"note {i} about docker")
    assert await backfill(db, FakeEmbedder()) == 300
    assert await backfill(db, FakeEmbedder()) == 0


def test_app_startup_indexes_old_chats(tmp_path):
    db = _db(tmp_path)
    for i in range(150):
        db.add_turn(f"c{i % 5}", "user", f"note {i} about docker")
    cfg = Config("http://x/v1", "m", tmp_path, [], 5, 10, "127.0.0.1:8700")
    app = create_app(cfg, db, lambda *a, **k: None, mem_path=tmp_path / "m.db", embedder=FakeEmbedder())
    with TestClient(app):
        end = time.time() + 5
        while time.time() < end and db._c.execute("select count(*) from turn_vectors").fetchone()[0] < 150:
            time.sleep(0.02)
    assert db._c.execute("select count(*) from turn_vectors").fetchone()[0] == 150
