from __future__ import annotations
import json
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from aon.engine import RunResult
from aon.llm_client import LLMClient
from aon.tools.base import Tool, ToolResult


@dataclass
class EvalCase:
    id: str
    prompt: str
    expected_output: str = ""
    files: list = field(default_factory=list)
    assertions: list = field(default_factory=list)


@dataclass
class TriggerQuery:
    query: str
    should_trigger: bool


def load_suite(store, name):
    data = json.loads(store.read(name, "evals/evals.json"))
    cases = [EvalCase(id=str(e.get("id", "")), prompt=e.get("prompt", ""),
                      expected_output=e.get("expected_output", ""),
                      files=list(e.get("files", [])), assertions=list(e.get("assertions", [])))
             for e in data.get("evals", [])]
    trigs = [TriggerQuery(query=t.get("query", ""), should_trigger=bool(t.get("should_trigger", False)))
             for t in data.get("triggers", [])]
    return cases, trigs


def did_trigger(run_result: RunResult, skill_name: str) -> bool:
    for st in run_result.steps:
        if st.tool == "read_skill" and isinstance(st.args, dict) and st.args.get("name") == skill_name:
            return True
    return False


class InstrumentedLLM:
    def __init__(self, inner):
        self.inner = inner
        self.tokens = 0
        self.calls = 0

    async def chat(self, messages, tools=None):
        res = await self.inner.chat(messages, tools=tools)
        self.calls += 1
        if getattr(res, "usage", None):
            self.tokens += int(res.usage.get("total_tokens", 0) or 0)
        return res

    async def list_models(self):
        return await self.inner.list_models()


_GRADE_SYS = ("You are a strict grader. Given OUTPUT and a list of ASSERTIONS, return a JSON array; "
              "one object per assertion: {\"text\":..., \"passed\":true|false, \"evidence\":\"quote/why\"}. "
              "Require concrete evidence for passed=true. Return ONLY the JSON array.")


def _extract_json_array(text: str):
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("no json array")


async def grade(assertions, outputs_text, llm):
    if not assertions:
        return []
    prompt = f"OUTPUT:\n{outputs_text}\n\nASSERTIONS:\n" + "\n".join(f"- {a}" for a in assertions)
    for _ in range(2):
        res = await llm.chat([{"role": "system", "content": _GRADE_SYS},
                              {"role": "user", "content": prompt}])
        try:
            arr = _extract_json_array(res.content)
            out = []
            for a, r in zip(assertions, arr):
                out.append({"text": a, "passed": bool(r.get("passed")),
                            "evidence": str(r.get("evidence", ""))})
            if len(out) == len(assertions):
                return out
        except (ValueError, json.JSONDecodeError, AttributeError):
            continue
    return [{"text": a, "passed": False, "evidence": "grader parse error"} for a in assertions]


def _read_outputs(ws: Path, limit=20000) -> str:
    parts = []
    for p in sorted(ws.rglob("*")):
        if p.is_file() and not p.name.startswith("_eval_"):
            try:
                parts.append(f"# {p.relative_to(ws)}\n{p.read_text()[:limit]}")
            except OSError:
                pass
    return "\n\n".join(parts)[:limit * 3]


class EvalRunner:
    def __init__(self, cfg, store, model=None, agent_factory=None):
        self.cfg = cfg
        self.store = store
        self.model = model or cfg.default_model
        self._factory = agent_factory

    def _factory_fn(self):
        if self._factory:
            return self._factory
        from aon.build import build_eval_agent
        return build_eval_agent

    def _make_skills_dir(self, skill, with_skill):
        tmp = Path(tempfile.mkdtemp(prefix="aon-sk-"))
        if with_skill:
            shutil.copytree(self.store._safe(skill), tmp / skill)
        return tmp

    async def run_once(self, case, skill, with_skill):
        ws = Path(tempfile.mkdtemp(prefix="aon-ws-"))
        skdir = None
        try:
            skdir = self._make_skills_dir(skill, with_skill)
            for f in case.files:
                src = self.store._in_skill(skill, f"evals/{f}")
                if src.exists():
                    shutil.copy(src, ws / Path(f).name)
            il = InstrumentedLLM(LLMClient(self.cfg.ollama_base_url, self.model))
            agent = self._factory_fn()(self.cfg, ws, skdir, self.model, il)
            t0 = time.monotonic()
            try:
                rr = await agent.run(case.prompt)
                err = None
            except Exception as e:  # sub-run failure: record, continue
                rr = RunResult("error", str(e), [])
                err = str(e)
            ms = int((time.monotonic() - t0) * 1000)
            return {"outputs_text": _read_outputs(ws), "tokens": il.tokens, "ms": ms,
                    "triggered": did_trigger(rr, skill), "error": err}
        except Exception as e:  # setup failure (e.g. missing skill): clean error, no leak
            return {"outputs_text": "", "tokens": 0, "ms": 0, "triggered": False, "error": str(e)}
        finally:
            shutil.rmtree(ws, ignore_errors=True)
            if skdir:
                shutil.rmtree(skdir, ignore_errors=True)

    async def grade_case(self, case, outputs_text):
        llm = LLMClient(self.cfg.ollama_base_url, self.model)
        return await grade(case.assertions, outputs_text, llm)

    async def trigger_rate(self, skill, query, runs):
        hits = 0
        for _ in range(runs):
            r = await self.run_once(EvalCase(id="t", prompt=query), skill, with_skill=True)
            if r["triggered"]:
                hits += 1
        return hits / runs if runs else 0.0

    async def run_suite(self, skill, output_runs=1, trigger_runs=3):
        cases, trigs = load_suite(self.store, skill)
        case_rows = []
        agg = {"with": {"pass": [], "ms": [], "tokens": []},
               "without": {"pass": [], "ms": [], "tokens": []}}
        for case in cases:
            row = {"id": case.id}
            for key, with_skill in (("with", True), ("without", False)):
                prates, mss, toks = [], [], []
                last_assert = []
                for _ in range(output_runs):
                    r = await self.run_once(case, skill, with_skill)
                    g = await self.grade_case(case, r["outputs_text"])
                    last_assert = g
                    pr = (sum(1 for a in g if a["passed"]) / len(g)) if g else 1.0
                    prates.append(pr)
                    mss.append(r["ms"])
                    toks.append(r["tokens"])
                pr = sum(prates) / len(prates)
                ms = sum(mss) // len(mss)
                tk = sum(toks) // len(toks)
                row[key] = {"pass_rate": pr, "ms": ms, "tokens": tk, "assertions": last_assert}
                agg[key]["pass"].append(pr)
                agg[key]["ms"].append(ms)
                agg[key]["tokens"].append(tk)
            case_rows.append(row)
        trig_rows = []
        passes = 0
        for t in trigs:
            rate = await self.trigger_rate(skill, t.query, trigger_runs)
            ok = (rate > 0.5) == t.should_trigger
            passes += 1 if ok else 0
            trig_rows.append({"query": t.query, "should_trigger": t.should_trigger,
                              "trigger_rate": rate, "pass": ok})

        def _mean(xs):
            return (sum(xs) / len(xs)) if xs else 0.0

        summ_with = {"pass_rate": _mean(agg["with"]["pass"]), "ms": int(_mean(agg["with"]["ms"])),
                     "tokens": int(_mean(agg["with"]["tokens"]))}
        summ_without = {"pass_rate": _mean(agg["without"]["pass"]), "ms": int(_mean(agg["without"]["ms"])),
                        "tokens": int(_mean(agg["without"]["tokens"]))}
        bench = {"skill": skill, "cases": case_rows, "triggers": trig_rows,
                 "summary": {"with": summ_with, "without": summ_without,
                             "delta": {"pass_rate": summ_with["pass_rate"] - summ_without["pass_rate"],
                                       "ms": summ_with["ms"] - summ_without["ms"],
                                       "tokens": summ_with["tokens"] - summ_without["tokens"]},
                             "trigger_pass_rate": (passes / len(trigs)) if trigs else 0.0}}
        self.store.write(skill, json.dumps(bench, indent=2), "evals/benchmark.json")
        return bench


class RunSkillEvalsTool(Tool):
    name = "run_skill_evals"
    description = ("Run a skill's eval suite (evals/evals.json): with-skill vs without-skill, "
                   "grade assertions, test description trigger-rate, write evals/benchmark.json. "
                   "Launches an auto-approving sandbox, so this action is gated.")
    parameters = {"type": "object", "properties": {
        "name": {"type": "string"},
        "output_runs": {"type": "integer"}, "trigger_runs": {"type": "integer"}},
        "required": ["name"]}
    mutating = True

    def __init__(self, cfg):
        self.cfg = cfg

    async def run(self, args):
        from aon.skills import SkillStore
        try:
            runner = EvalRunner(self.cfg, SkillStore(Path("skills")))
            b = await runner.run_suite(args["name"], int(args.get("output_runs", 1) or 1),
                                       int(args.get("trigger_runs", 3) or 3))
            d = b["summary"]["delta"]
            return ToolResult(ok=True, output=(
                f"evals for '{args['name']}': with={b['summary']['with']['pass_rate']:.2f} "
                f"without={b['summary']['without']['pass_rate']:.2f} "
                f"delta={d['pass_rate']:+.2f} pass, trigger_pass_rate="
                f"{b['summary']['trigger_pass_rate']:.2f}. Report in evals/benchmark.json."))
        except (ValueError, OSError, KeyError) as e:
            return ToolResult(ok=False, output="", error=str(e))
