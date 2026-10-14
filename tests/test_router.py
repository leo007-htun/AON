import json
import pytest
from aon.router import Plan, deliberate, render_plan_hint
from aon.llm_client import ChatResult


class FakeLLM:
    def __init__(self, c): self.c = c
    async def chat(self, m, tools=None): return ChatResult(content=self.c)


@pytest.mark.asyncio
async def test_deliberate_parses_plan():
    d = json.dumps({"understanding": "turn url into skill", "intent": "create_skill_from_url",
                    "plan": [{"tool": "draft_skill", "args": {"url": "http://x"}}],
                    "needs_clarification": False, "question": None, "direct_answer": None})
    p = await deliberate("make a skill from http://x", FakeLLM(d), valid_tools={"draft_skill"})
    assert p.intent == "create_skill_from_url" and p.plan[0]["tool"] == "draft_skill"
    assert p.plan[0]["args"]["url"] == "http://x"


@pytest.mark.asyncio
async def test_deliberate_drops_unknown_tool():
    d = json.dumps({"understanding": "x", "intent": "t",
                    "plan": [{"tool": "nuke_world", "args": {}}],
                    "needs_clarification": False})
    p = await deliberate("go", FakeLLM(d), valid_tools={"draft_skill"})
    assert p.plan == []


@pytest.mark.asyncio
async def test_deliberate_underspecified_asks():
    d = json.dumps({"understanding": "wants a skill but no url", "intent": "create_skill_from_url",
                    "plan": [], "needs_clarification": True,
                    "question": "Which URL should I turn into a skill?"})
    p = await deliberate("make a skill from a url", FakeLLM(d))
    assert p.needs_clarification and "URL" in p.question and p.plan == []


@pytest.mark.asyncio
async def test_deliberate_direct_answer():
    d = json.dumps({"understanding": "greeting", "intent": "answer", "plan": [],
                    "needs_clarification": False, "direct_answer": "Hello!"})
    p = await deliberate("hi", FakeLLM(d))
    assert p.intent == "answer" and p.direct_answer == "Hello!"


@pytest.mark.asyncio
async def test_deliberate_bad_json_falls_back():
    p = await deliberate("x", FakeLLM("not json"))
    assert p.intent == "task" and not p.needs_clarification and p.plan == []


def test_render_plan_hint():
    p = Plan("turn url into skill", "create_skill_from_url",
             [{"tool": "draft_skill", "args": {"url": "http://x"}}], False, None, None)
    h = render_plan_hint(p)
    assert "draft_skill" in h and "http://x" in h and "Intent" in h


def test_render_plan_hint_empty_for_answer_and_clarify():
    assert render_plan_hint(Plan("", "answer", [], False, None, "hi")) == ""
    assert render_plan_hint(Plan("", "x", [], True, "q?", None)) == ""
