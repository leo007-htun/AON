import json
import pytest
from agent.skills import SkillStore, WriteSkillTool
from agent.skill_evals import (EvalCase, TriggerQuery, load_suite, InstrumentedLLM,
                               did_trigger, grade)
from agent.engine import RunResult, Step
from agent.llm_client import ChatResult


@pytest.mark.asyncio
async def test_load_suite(tmp_path):
    s = SkillStore(tmp_path)
    await WriteSkillTool(s).run({"name": "d", "content": "body"})
    suite = {"evals": [{"id": "1", "prompt": "p", "expected_output": "e",
                        "assertions": ["a1"], "files": ["f.txt"]}],
             "triggers": [{"query": "q", "should_trigger": True}]}
    await WriteSkillTool(s).run({"name": "d", "content": json.dumps(suite), "path": "evals/evals.json"})
    cases, trigs = load_suite(s, "d")
    assert cases[0].id == "1" and cases[0].assertions == ["a1"] and cases[0].files == ["f.txt"]
    assert trigs[0].query == "q" and trigs[0].should_trigger is True


def test_did_trigger():
    rr = RunResult("done", "x", [Step("read_skill", {"name": "d"}, "...", True)])
    assert did_trigger(rr, "d") is True
    assert did_trigger(RunResult("done", "x", [Step("list_dir", {}, "", True)]), "d") is False


class FakeLLM:
    def __init__(self, content):
        self._c = content

    async def chat(self, messages, tools=None):
        return ChatResult(content=self._c, usage={"total_tokens": 5})


@pytest.mark.asyncio
async def test_grade_parses_verdict():
    verdict = json.dumps([{"text": "a1", "passed": True, "evidence": "found"}])
    res = await grade(["a1"], "some output", FakeLLM(verdict))
    assert res[0]["passed"] is True and res[0]["text"] == "a1"


@pytest.mark.asyncio
async def test_grade_parse_error_fails_safe():
    res = await grade(["a1"], "out", FakeLLM("not json at all"))
    assert res[0]["passed"] is False


@pytest.mark.asyncio
async def test_instrumented_accumulates():
    il = InstrumentedLLM(FakeLLM('{"x":1}'))
    await il.chat([])
    await il.chat([])
    assert il.calls == 2 and il.tokens == 10
