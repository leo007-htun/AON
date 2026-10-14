"""Memory facts found by meaning (embeddings), with keyword fallback; near-duplicate facts are
judged by the model and the outdated one is retired."""
import pytest
from aon.memory import EpisodicMemory
from aon.memory_semantic import (index_episodes, search_episodes, SemanticMemory, consolidate)
from aon.memory_extract import SearchMemoryTool
from aon.llm_client import ChatResult
from aon.engine import Agent
from aon.approval_gate import AutoGate
from aon.tools.base import ToolRegistry

# a toy embedder that knows a few synonyms, so "food" and "pizza" land on the same axis
AXES = {"colour": "col", "color": "col", "teal": "col", "blue": "col", "food": "eat", "pizza": "eat",
        "pasta": "eat", "enjoy": "eat", "port": "net", "listening": "net", "dog": "pet", "max": "pet"}
DIMS = ["col", "eat", "net", "pet"]


class FakeEmbedder:
    model = "fake-embed"
    def __init__(self, fail=False): self.fail = fail; self.calls = 0
    async def embed(self, texts):
        if self.fail:
            raise RuntimeError("down")
        self.calls += 1
        out = []
        for t in texts:
            words = [w.strip(".,?!'") for w in t.lower().split()]
            out.append([sum(1 for w in words if AXES.get(w) == d) for d in DIMS] + [0.05])
        return out


class Judge:
    def __init__(self, answer="DIFFERENT", fail=False): self.answer = answer; self.fail = fail; self.calls = []
    async def chat(self, messages, tools=None):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("model down")
        return ChatResult(self.answer, [])


def _mem(tmp_path):
    return EpisodicMemory(tmp_path / "e.db")


@pytest.mark.asyncio
async def test_facts_are_embedded_once(tmp_path):
    m = _mem(tmp_path); m.add("User loves pizza"); m.add("User's dog is Max")
    e = FakeEmbedder()
    assert await index_episodes(m, e) == 2
    assert await index_episodes(m, e) == 0


@pytest.mark.asyncio
async def test_search_finds_a_fact_with_no_shared_words(tmp_path):
    m = _mem(tmp_path); m.add("User loves pizza"); m.add("AON runs on port 8700")
    assert m.search("what food do I enjoy?") == []                 # keyword search misses it
    rows = await search_episodes(m, FakeEmbedder(), "what food do I enjoy?")
    assert rows and rows[0]["content"] == "User loves pizza"


@pytest.mark.asyncio
async def test_outdated_facts_are_not_returned(tmp_path):
    m = _mem(tmp_path); i = m.add("User loves pizza"); m.mark_outdated(i)
    assert await search_episodes(m, FakeEmbedder(), "what food do I enjoy?") == []


@pytest.mark.asyncio
async def test_prepared_memory_puts_the_meaning_match_in_context(tmp_path):
    m = _mem(tmp_path); m.add("User loves pizza")
    for i in range(8):
        m.add(f"unrelated note {i}")
    sm = SemanticMemory(m, FakeEmbedder())
    assert "pizza" not in sm.context_for("what food do I enjoy?")    # not prepared: keyword + recent only
    await sm.prepare("what food do I enjoy?")
    assert "Relevant to this request:\n- [fact] User loves pizza" in sm.context_for("what food do I enjoy?")


@pytest.mark.asyncio
async def test_failing_embedder_falls_back_to_keywords(tmp_path):
    m = _mem(tmp_path); m.add("User loves pizza")
    sm = SemanticMemory(m, FakeEmbedder(fail=True))
    await sm.prepare("pizza please")
    assert "User loves pizza" in sm.context_for("pizza please")


def test_semantic_memory_passes_other_calls_through(tmp_path):
    m = _mem(tmp_path)
    sm = SemanticMemory(m, FakeEmbedder())
    i = sm.add("User loves pizza")
    assert sm.get(i)["content"] == "User loves pizza"


@pytest.mark.asyncio
async def test_a_replacing_fact_retires_the_old_one(tmp_path):
    m = _mem(tmp_path)
    old = m.add("User's favourite colour is teal")
    e = FakeEmbedder(); await index_episodes(m, e)
    new = m.add("User's favourite colour is blue")
    judge = Judge("REPLACES")
    await consolidate(m, e, judge)
    assert m.get(old)["active"] == 0 and m.get(old)["superseded_by"] == new
    assert m.get(new)["active"] == 1
    p = "\n".join(x["content"] for x in judge.calls[0])
    assert "teal" in p and "blue" in p


@pytest.mark.asyncio
async def test_a_repeated_fact_keeps_only_the_first(tmp_path):
    m = _mem(tmp_path)
    old = m.add("User loves pizza"); e = FakeEmbedder(); await index_episodes(m, e)
    new = m.add("User said they love pizza")
    await consolidate(m, e, Judge("SAME"))
    assert m.get(old)["active"] == 1 and m.get(new)["active"] == 0


@pytest.mark.asyncio
async def test_facts_that_can_both_be_true_are_kept(tmp_path):
    m = _mem(tmp_path)
    a = m.add("User loves pizza"); e = FakeEmbedder(); await index_episodes(m, e)
    b = m.add("User loves pasta")
    await consolidate(m, e, Judge("DIFFERENT"))
    assert m.get(a)["active"] == 1 and m.get(b)["active"] == 1


@pytest.mark.asyncio
async def test_unrelated_facts_never_reach_the_judge(tmp_path):
    m = _mem(tmp_path)
    m.add("User loves pizza"); e = FakeEmbedder(); await index_episodes(m, e)
    m.add("AON runs on port 8700")
    judge = Judge("REPLACES")
    await consolidate(m, e, judge)
    assert judge.calls == [] and len(m.all()) == 2


@pytest.mark.asyncio
async def test_judge_failure_keeps_both(tmp_path):
    m = _mem(tmp_path)
    a = m.add("User's favourite colour is teal"); e = FakeEmbedder(); await index_episodes(m, e)
    b = m.add("User's favourite colour is blue")
    await consolidate(m, e, Judge(fail=True))
    assert m.get(a)["active"] == 1 and m.get(b)["active"] == 1


@pytest.mark.asyncio
async def test_judge_reply_with_thinking_is_parsed(tmp_path):
    m = _mem(tmp_path)
    a = m.add("User's favourite colour is teal"); e = FakeEmbedder(); await index_episodes(m, e)
    m.add("User's favourite colour is blue")
    await consolidate(m, e, Judge("<think>they conflict</think>\nReplaces."))
    assert m.get(a)["active"] == 0


@pytest.mark.asyncio
async def test_search_memory_tool_by_meaning_with_keyword_fallback(tmp_path):
    m = _mem(tmp_path); m.add("User loves pizza")
    out = (await SearchMemoryTool(m, embedder=FakeEmbedder()).run({"query": "what food do I enjoy?"})).output
    assert "pizza" in out
    out = (await SearchMemoryTool(m, embedder=FakeEmbedder(fail=True)).run({"query": "pizza"})).output
    assert "pizza" in out


@pytest.mark.asyncio
async def test_agent_prepares_memory_before_planning():
    order = []
    class Mem:
        async def prepare(self, q): order.append(("prepare", q))
        def context_for(self, q): order.append(("context", q)); return ""
    async def router(goal, conversation=None):
        order.append(("router", goal)); return None
    class LLM:
        async def chat(self, m, tools=None): return ChatResult("ok", [])
    await Agent(LLM(), ToolRegistry(), AutoGate(), max_steps=2, memory=Mem(), router=router).run("hi there")
    assert order[0] == ("prepare", "hi there") and order[1] == ("router", "hi there")


def test_web_consolidates_memory_after_the_reply(tmp_path, monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from aon.config import Config
    from aon.persistence import Database
    from aon.web import create_app
    import aon.web as W
    seen = {}

    class FakeAgent:
        def __init__(self, on_event): self.on_event = on_event
        async def run(self, task, conversation=None, **kw):
            await self.on_event({"type": "final", "status": "done", "final": "ok"})
            return type("R", (), {"status": "done", "final": "ok"})()

    async def fake_consolidate(mem, embedder, llm, **k):
        seen["c"] = (str(mem.path), embedder.model, llm.model)
        return []
    monkeypatch.setattr(W, "consolidate", fake_consolidate)
    cfg = Config("http://x/v1", "m", tmp_path, [], 5, 10, "127.0.0.1:8700")
    app = create_app(cfg, Database(str(tmp_path / "d.db")),
                     lambda gate, on_event, model, convo="default": FakeAgent(on_event),
                     mem_path=tmp_path / "m.db", embedder=FakeEmbedder())
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"type": "goal", "goal": "my favourite colour is teal", "model": "AON:latest", "session_id": "c"})
        while ws.receive_json()["type"] != "final":
            pass
        end = time.time() + 5
        while "c" not in seen and time.time() < end:
            time.sleep(0.02)
    assert seen["c"] == (str(tmp_path / "m.db"), "fake-embed", "AON:latest")
