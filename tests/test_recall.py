"""Semantic recall: chat turns get embeddings in the database; relevant older turns come back."""
import math
import pytest
from agent.persistence import Database
from agent.recall import index_turns, recall, recall_background, Embedder
from agent.memory_extract import SearchConversationTool
from agent.context import estimate_tokens

VOCAB = ["port", "rust", "pizza", "weather", "docker", "cat", "colo"]


class FakeEmbedder:
    model = "fake-embed"
    def __init__(self, fail=False): self.fail = fail; self.calls = []
    async def embed(self, texts):
        if self.fail:
            raise RuntimeError("embedder down")
        self.calls.append(list(texts))
        return [[t.lower().count(w) for w in VOCAB] + [0.05] for t in texts]


def _db(tmp_path):
    return Database(str(tmp_path / "d.db"))


def _chat(db, convo="c"):
    texts = ["I set the docker port to 8123 for the server", "Noted, docker port 8123.",
             "my favourite food is pizza with extra cheese", "Pizza noted!",
             "what is the weather like in Liverpool today", "Cloudy, 14 degrees.",
             "hi", "Hello!"]
    return [db.add_turn(convo, "user" if i % 2 == 0 else "assistant", t) for i, t in enumerate(texts)]


@pytest.mark.asyncio
async def test_turns_are_embedded_once(tmp_path):
    db = _db(tmp_path); _chat(db)
    e = FakeEmbedder()
    assert await index_turns(db, e, "c") == 8
    assert await index_turns(db, e, "c") == 0
    assert all(t.startswith("search_document: ") for t in e.calls[0])


@pytest.mark.asyncio
async def test_recall_finds_the_relevant_older_turn(tmp_path):
    db = _db(tmp_path); ids = _chat(db)
    e = FakeEmbedder()
    await index_turns(db, e, "c")
    hits = await recall(db, e, "c", "which port did I give docker?", exclude_ids=set(ids[6:]))
    assert hits and "8123" in hits[0]["content"]
    assert not any("pizza" in h["content"].lower() for h in hits)
    assert e.calls[-1][0].startswith("search_query: ")


@pytest.mark.asyncio
async def test_recall_skips_turns_already_in_the_request(tmp_path):
    db = _db(tmp_path); ids = _chat(db)
    e = FakeEmbedder()
    await index_turns(db, e, "c")
    hits = await recall(db, e, "c", "docker port", exclude_ids={ids[0], ids[1]})
    assert all(h["id"] not in (ids[0], ids[1]) for h in hits)


@pytest.mark.asyncio
async def test_recall_respects_the_token_budget_and_keeps_chat_order(tmp_path):
    db = _db(tmp_path)
    ids = [db.add_turn("c", "user", f"docker port note {i} " + "docker " * 400) for i in range(10)]
    e = FakeEmbedder()
    await index_turns(db, e, "c")
    hits = await recall(db, e, "c", "docker port", exclude_ids=set(), budget=1500)
    assert hits and sum(estimate_tokens(h["content"]) for h in hits) <= 1500
    assert [h["id"] for h in hits] == sorted(h["id"] for h in hits)


@pytest.mark.asyncio
async def test_recall_stays_inside_the_chat(tmp_path):
    db = _db(tmp_path); _chat(db, "other")
    db.add_turn("c", "user", "hello there")
    e = FakeEmbedder()
    await index_turns(db, e, "c"); await index_turns(db, e, "other")
    assert await recall(db, e, "c", "docker port", exclude_ids=set()) == []


@pytest.mark.asyncio
async def test_failing_embedder_means_no_recall(tmp_path):
    db = _db(tmp_path); _chat(db)
    assert await index_turns(db, FakeEmbedder(fail=True), "c") == 0
    assert await recall(db, FakeEmbedder(fail=True), "c", "docker port", exclude_ids=set()) == []


@pytest.mark.asyncio
async def test_vectors_from_another_embedding_model_are_not_mixed(tmp_path):
    db = _db(tmp_path); _chat(db)
    e = FakeEmbedder(); await index_turns(db, e, "c")
    e2 = FakeEmbedder(); e2.model = "other-embed"
    assert await index_turns(db, e2, "c") == 8


@pytest.mark.asyncio
async def test_clearing_a_chat_removes_its_vectors(tmp_path):
    db = _db(tmp_path); _chat(db)
    e = FakeEmbedder(); await index_turns(db, e, "c")
    db.clear_conversation("c")
    _chat(db)
    assert await index_turns(db, e, "c") == 8


@pytest.mark.asyncio
async def test_background_lists_recalled_turns(tmp_path):
    db = _db(tmp_path); ids = _chat(db)
    e = FakeEmbedder(); await index_turns(db, e, "c")
    bg = await recall_background(db, e, "c", "docker port?", exclude_ids=set(ids[6:]))
    assert "8123" in bg and "earlier" in bg.lower()
    assert await recall_background(db, e, "c", "docker port?", exclude_ids=set(ids)) == ""


@pytest.mark.asyncio
async def test_search_conversation_tool_is_semantic_with_keyword_fallback(tmp_path):
    db = _db(tmp_path); _chat(db)
    e = FakeEmbedder(); await index_turns(db, e, "c")
    out = (await SearchConversationTool(db, "c", embedder=e).run({"query": "docker port"})).output
    assert "8123" in out
    out = (await SearchConversationTool(db, "c", embedder=FakeEmbedder(fail=True)).run({"query": "pizza"})).output
    assert "pizza" in out.lower()


@pytest.mark.asyncio
async def test_embedder_normalises_and_prefixes(monkeypatch):
    import httpx
    sent = {}
    def handler(request):
        import json
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [3.0, 4.0]}]})
    e = Embedder("http://x/v1", "nomic-embed-text", transport=httpx.MockTransport(handler))
    v = (await e.embed(["search_query: hi"]))[0]
    assert sent["body"] == {"model": "nomic-embed-text", "input": ["search_query: hi"]}
    assert math.isclose(math.hypot(*v), 1.0)


def _web(tmp_path, embedder, seen, history_tokens=8000, cross_chat=True):
    from fastapi.testclient import TestClient
    from agent.config import Config
    from agent.web import create_app
    db = _db(tmp_path)

    class FakeAgent:
        def __init__(self, on_event): self.on_event = on_event
        async def run(self, task, conversation=None, **kw):
            seen["kw"] = kw; seen["conv"] = conversation
            await self.on_event({"type": "final", "status": "done", "final": "ok"})
            return type("R", (), {"status": "done", "final": "ok"})()

    cfg = Config("http://x/v1", "m", tmp_path, [], 5, 10, "127.0.0.1:8700", history_tokens=history_tokens,
                 cross_chat_recall=cross_chat)
    app = create_app(cfg, db, lambda gate, on_event, model, convo="default": FakeAgent(on_event),
                     mem_path=tmp_path / "m.db", embedder=embedder)
    return TestClient(app), db


def _ask(client, goal):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "goal", "goal": goal, "model": "m", "session_id": "c"})
        while ws.receive_json()["type"] != "final":
            pass


def test_web_recalls_relevant_turns_outside_the_window(tmp_path):
    seen, e = {}, FakeEmbedder()
    client, db = _web(tmp_path, e, seen)
    db.add_turn("c", "user", "I set the docker port to 8123 for the server")
    for i in range(30):
        db.add_turn("c", "user" if i % 2 else "assistant", f"small talk {i} about the weather")
    _ask(client, "which docker port did I pick?")
    assert "8123" in seen["kw"]["background"]
    assert all("8123" not in t["content"] for t in seen["conv"])
    assert all(set(t) == {"role", "content"} for t in seen["conv"])


def test_web_short_chat_makes_no_embedding_calls(tmp_path):
    # (with cross-chat recall off: that one embeds every message by design)
    seen, e = {}, FakeEmbedder()
    client, db = _web(tmp_path, e, seen, cross_chat=False)
    db.add_turn("c", "user", "docker port 8123"); db.add_turn("c", "assistant", "ok")
    _ask(client, "which docker port?")
    assert e.calls == [] and "background" not in seen["kw"]


def test_web_recalls_turns_the_token_budget_left_out(tmp_path):
    seen, e = {}, FakeEmbedder()
    client, db = _web(tmp_path, e, seen, history_tokens=3000)
    db.add_turn("c", "user", "I set the docker port to 8123 for the server")
    for i in range(6):
        db.add_turn("c", "assistant", f"weather report {i} " + "w" * 3000)
    _ask(client, "which docker port did I pick?")
    assert "8123" in seen["kw"].get("background", "")


@pytest.mark.asyncio
async def test_a_recalled_question_brings_its_answer(tmp_path):
    db = _db(tmp_path)
    q = db.add_turn("c", "user", "what is my fav color")
    a = db.add_turn("c", "assistant", "Teal")
    for i in range(6):
        db.add_turn("c", "user", f"pizza talk {i} with more words")
    e = FakeEmbedder(); await index_turns(db, e, "c")
    hits = await recall(db, e, "c", "what is my favourite colour?", exclude_ids=set())
    assert [h["id"] for h in hits] == [q, a] and hits[1]["content"] == "Teal"


@pytest.mark.asyncio
async def test_a_recalled_answer_brings_its_question(tmp_path):
    db = _db(tmp_path)
    q = db.add_turn("c", "user", "and the other one?")
    a = db.add_turn("c", "assistant", "The docker port is 8123")
    e = FakeEmbedder(); await index_turns(db, e, "c")
    hits = await recall(db, e, "c", "docker port", exclude_ids=set())
    assert [h["id"] for h in hits] == [q, a]


@pytest.mark.asyncio
async def test_the_partner_turn_is_not_repeated_when_already_sent(tmp_path):
    db = _db(tmp_path)
    q = db.add_turn("c", "user", "what is my fav color")
    a = db.add_turn("c", "assistant", "Teal")
    e = FakeEmbedder(); await index_turns(db, e, "c")
    hits = await recall(db, e, "c", "favourite colour", exclude_ids={a})
    assert [h["id"] for h in hits] == [q]


@pytest.mark.asyncio
async def test_the_best_match_is_not_clipped_when_the_budget_allows(tmp_path):
    db = _db(tmp_path)
    table = "docker port table " + " | ".join(f"row{i} docker 81{i:02d}" for i in range(150))   # ~900 tokens
    db.add_turn("c", "user", "list the docker ports")
    t = db.add_turn("c", "assistant", table)
    e = FakeEmbedder(); await index_turns(db, e, "c")
    hits = await recall(db, e, "c", "docker port", exclude_ids=set(), budget=1500)
    best = [h for h in hits if h["id"] == t][0]
    assert best["content"] == table
