import json
import pytest
from agent.memory import EpisodicMemory
from agent.memory_extract import (extract_episodes, maybe_extract,
                                  SearchMemoryTool, LearnUrlTool)
from agent.llm_client import ChatResult


class FakeLLM:
    def __init__(self, content):
        self._c = content
    async def chat(self, messages, tools=None):
        return ChatResult(content=self._c)


@pytest.mark.asyncio
async def test_extract_parses(tmp_path):
    out = json.dumps([{"type": "preference", "content": "likes dark mode", "tags": "ui"}])
    eps = await extract_episodes("conv", FakeLLM(out))
    assert eps[0]["content"] == "likes dark mode" and eps[0]["type"] == "preference"


@pytest.mark.asyncio
async def test_extract_junk_is_empty(tmp_path):
    assert await extract_episodes("x", FakeLLM("no json here")) == []


@pytest.mark.asyncio
async def test_maybe_extract_stores_and_never_raises(tmp_path):
    m = EpisodicMemory(tmp_path / "e.db")
    class Boom:
        async def chat(self, *a, **k):
            raise RuntimeError("down")
    await maybe_extract(m, Boom(), "goal", "final")  # must not raise
    out = json.dumps([{"type": "fact", "content": "project uses postgres", "tags": "db"}])
    await maybe_extract(m, FakeLLM(out), "g", "f")
    assert any("postgres" in e["content"] for e in m.all())


@pytest.mark.asyncio
async def test_search_memory_tool(tmp_path):
    m = EpisodicMemory(tmp_path / "e.db")
    m.add("database is postgres", tags="db")
    r = await SearchMemoryTool(m).run({"query": "postgres"})
    assert r.ok and "postgres" in r.output


@pytest.mark.asyncio
async def test_learn_url_stores_and_flags_skill(tmp_path):
    m = EpisodicMemory(tmp_path / "e.db")
    async def fetch(url):
        return "Step 1: install. Step 2: run. A how-to procedure."
    out = json.dumps([{"type": "reference", "content": "playwright needs node", "tags": "playwright"}])
    r = await LearnUrlTool(fetch, FakeLLM(out), m).run({"url": "http://x/doc"})
    assert r.ok and "playwright" in m.as_prompt_text(10)
    assert "skill" in r.output.lower()
