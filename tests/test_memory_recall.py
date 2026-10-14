import pytest
from aon.memory import EpisodicMemory, RememberTool
from aon.engine import Agent
from aon.router import Plan, _SYS
from aon.llm_client import ChatResult
from aon.tools.base import ToolRegistry
from aon.approval_gate import AutoGate
from aon.memory_extract import _EXTRACT_SYS, _CONVO_RULE


def _mem(tmp_path):
    m = EpisodicMemory(tmp_path / "e.db")
    m.add("Favorite color is teal", type="preference")
    m.add("User expresses desire to visit Iceland", type="preference")
    for i in range(12):                       # newer noise pushes the facts out of recent()
        m.add(f"noise fact number {i} about the workspace", type="fact")
    return m


def test_search_ignores_stopwords(tmp_path):
    m = _mem(tmp_path)
    assert [r["content"] for r in m.search("what is my fav color")] == ["Favorite color is teal"]
    assert m.search("where do i want to visit")[0]["content"].endswith("visit Iceland")
    assert m.search("what is it") == []


def test_context_for_includes_relevant_beyond_recent(tmp_path):
    m = _mem(tmp_path)
    assert "teal" not in m.as_prompt_text()   # the old recency-only block missed it
    ctx = m.context_for("what is my fav color?")
    assert "Favorite color is teal" in ctx
    assert ctx.count("Favorite color is teal") == 1


def test_remember_schema_declares_content():
    assert "content" in RememberTool(None).parameters["properties"]


class LLM:
    def __init__(self, seq): self.seq = seq; self.i = 0; self.sys = None
    async def chat(self, m, tools=None):
        self.sys = m[0]["content"]; r = self.seq[min(self.i, len(self.seq) - 1)]; self.i += 1; return r


def _router(plan):
    async def r(goal, conversation=None): return plan
    return r


@pytest.mark.asyncio
async def test_recall_intent_does_not_stop_at_clarify(tmp_path):
    llm = LLM([ChatResult("Your favorite color is teal.", [])])
    plan = Plan("user asks their favorite color", "recall", [], True,
                "Could you tell me what your favorite color is?", None)
    res = await Agent(llm, ToolRegistry(), AutoGate(), memory=_mem(tmp_path),
                      router=_router(plan)).run("what is my fav color")
    assert res.final == "Your favorite color is teal."
    assert "Favorite color is teal" in llm.sys


@pytest.mark.asyncio
async def test_recall_intent_does_not_stop_at_direct_answer(tmp_path):
    llm = LLM([ChatResult("You want to visit Iceland.", [])])
    plan = Plan("", "recall", [], False, None, "I don't have that information.")
    res = await Agent(llm, ToolRegistry(), AutoGate(), memory=_mem(tmp_path),
                      router=_router(plan)).run("where do i want to visit")
    assert res.final == "You want to visit Iceland."


def test_router_prompt_forbids_asking_user_to_repeat():
    assert "search_memory" in _SYS and "never ask the user to repeat" in _SYS.lower()


def test_extract_prompt_only_user_statements():
    assert "only what the user" in _CONVO_RULE.lower()
    assert "only what the user" not in _EXTRACT_SYS.lower()   # learn_url pages keep the general rule


@pytest.mark.asyncio
async def test_maybe_extract_uses_user_only_rule(tmp_path):
    from aon.memory_extract import maybe_extract
    seen = {}
    class L:
        async def chat(self, m, tools=None):
            seen["sys"] = m[0]["content"]; return ChatResult("[]", [])
    await maybe_extract(EpisodicMemory(tmp_path / "e.db"), L(), "I like tea", "Try Italy!")
    assert seen["sys"].startswith(_CONVO_RULE)


def test_router_prompt_treats_statements_as_remember():
    assert "do not ask what to do with it" in _SYS
