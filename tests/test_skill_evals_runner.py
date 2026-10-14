import json
import pytest
from agent.config import Config
from agent.skills import SkillStore, WriteSkillTool
from agent.skill_evals import EvalRunner, RunSkillEvalsTool, EvalCase
from agent.engine import RunResult, Step


def _cfg(tmp):
    return Config("http://x/v1", "m", tmp / "ws", [], 5, 10, "127.0.0.1:8700")


async def _mk_skill(tmp):
    s = SkillStore(tmp / "skills")
    await WriteSkillTool(s).run({"name": "demo", "content": "---\nname: demo\ndescription: d\n---\nuse WHERE x"})
    suite = {"evals": [{"id": "c1", "prompt": "do it", "assertions": ["mentions WHERE"]}],
             "triggers": [{"query": "please do it", "should_trigger": True},
                          {"query": "unrelated", "should_trigger": False}]}
    await WriteSkillTool(s).run({"name": "demo", "content": json.dumps(suite), "path": "evals/evals.json"})
    return s


class FakeRunner(EvalRunner):
    async def run_once(self, case, skill, with_skill):
        txt = "SQL with WHERE clause" if with_skill else "SQL no filter"
        return {"outputs_text": txt, "tokens": 10 if with_skill else 8, "ms": 5,
                "triggered": with_skill, "error": None}

    async def grade_case(self, case, outputs_text):
        passed = "WHERE" in outputs_text
        return [{"text": "mentions WHERE", "passed": passed, "evidence": outputs_text}]

    async def trigger_rate(self, skill, query, runs):
        return 1.0 if query == "please do it" else 0.0


@pytest.mark.asyncio
async def test_run_suite_aggregates(tmp_path):
    (tmp_path / "ws").mkdir()
    s = await _mk_skill(tmp_path)
    bench = await FakeRunner(_cfg(tmp_path), s).run_suite("demo", output_runs=1, trigger_runs=1)
    assert bench["summary"]["with"]["pass_rate"] == 1.0
    assert bench["summary"]["without"]["pass_rate"] == 0.0
    assert bench["summary"]["delta"]["pass_rate"] == 1.0
    assert bench["summary"]["trigger_pass_rate"] == 1.0
    assert json.loads(s.read("demo", "evals/benchmark.json"))["skill"] == "demo"


def test_tool_is_gated(tmp_path):
    assert RunSkillEvalsTool(_cfg(tmp_path)).mutating is True


class _FakeAgent:
    def __init__(self, ws, skdir, rec):
        self.ws = ws
        self.skdir = skdir
        self.rec = rec

    async def run(self, prompt):
        from pathlib import Path
        (Path(self.ws) / "out.txt").write_text("has WHERE clause")
        self.rec["ws"] = str(self.ws)
        self.rec["skill_present"] = (Path(self.skdir) / "demo" / "SKILL.md").exists()
        return RunResult("done", "ok", [Step("read_skill", {"name": "demo"}, "", True)])


@pytest.mark.asyncio
async def test_run_once_isolation_and_cleanup(tmp_path):
    (tmp_path / "ws").mkdir()
    s = await _mk_skill(tmp_path)
    rec_with, rec_wo = {}, {}
    r1 = await EvalRunner(_cfg(tmp_path), s,
            agent_factory=lambda cfg, ws, skdir, model, llm: _FakeAgent(ws, skdir, rec_with)
         ).run_once(EvalCase(id="c", prompt="p"), "demo", True)
    r2 = await EvalRunner(_cfg(tmp_path), s,
            agent_factory=lambda cfg, ws, skdir, model, llm: _FakeAgent(ws, skdir, rec_wo)
         ).run_once(EvalCase(id="c", prompt="p"), "demo", False)
    assert rec_with["skill_present"] is True
    assert rec_wo["skill_present"] is False
    assert "WHERE" in r1["outputs_text"] and r1["triggered"] is True
    from pathlib import Path
    assert not Path(rec_with["ws"]).exists()
    assert not Path(rec_wo["ws"]).exists()


# --- Final review fixes ---
@pytest.mark.asyncio
async def test_eval_agent_excludes_run_skill_evals(tmp_path):
    from agent.build import build_registry, build_eval_agent
    cfg = _cfg(tmp_path)
    assert "run_skill_evals" in {t.name for t in build_registry(cfg).list()}
    (tmp_path / "ws").mkdir()
    a = build_eval_agent(cfg, tmp_path / "ws", tmp_path / "sk", "m", object())
    assert "run_skill_evals" not in {t.name for t in a.registry.list()}


@pytest.mark.asyncio
async def test_run_once_missing_skill_errors_cleanly(tmp_path):
    (tmp_path / "ws").mkdir()
    s = await _mk_skill(tmp_path)
    r = await EvalRunner(_cfg(tmp_path), s,
            agent_factory=lambda cfg, ws, skdir, model, llm: _FakeAgent(ws, skdir, {})
        ).run_once(EvalCase(id="c", prompt="p"), "ghost", True)
    assert r["error"] and r["triggered"] is False
