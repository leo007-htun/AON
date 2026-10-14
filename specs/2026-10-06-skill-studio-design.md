# AON Skill Studio — design

**Date:** 2026-10-06
**Status:** for review
**Builds on:** `~/app` on `.84`, branch `foundation`. Current skills subsystem: `skills.py` (folder/`SKILL.md` + frontmatter), `build.py`, `web.py`, `static/index.html`.

## Goal

Give AON the full Agent Skills creation lifecycle, so that whenever a new skill is
needed it produces a *high-quality* one following the published methodology
(agentskills.io): multi-file skills, bundled scripts, calibrated descriptions,
and **fully-automated evaluation** (with-skill vs without-skill, assertion
grading, trigger-rate testing, benchmark deltas).

## Non-goals / invariants

- **Guardrails untouched.** `executor.py`, `approval_gate.py`, `telegram.py` and
  `GUARDRAIL_PATHS` are NOT modified.
- The **interactive chat gate is unchanged.** Auto-approval exists *only* inside
  the eval sandbox.
- No new backend services; same FastAPI app, Ollama, SQLite.

## Decisions (locked with the user)

1. Evals are **fully automated**: AON spawns isolated with/without sub-runs, grades
   assertions, computes trigger-rates and benchmark deltas.
2. **One spec + plan, build all.**
3. **Eval sandbox auto-approves everything, including `run_command`**, inside a
   disposable, path-confined, output/time-bounded temp workspace. Residual risk:
   `run_command` is real `bash` and can reach the host unattended during an eval.
   Accepted for this single-user self-hosted box. Launching an eval run is itself a
   **gated** action in the interactive agent (you approve starting the sandbox; the
   sandbox auto-approves within itself).

---

## Architecture

Five units, each with one responsibility.

### 1. `SkillStore` v2 — multi-file skills (`src/agent/skills.py`, modify)

A skill is a folder `skills/<name>/` containing `SKILL.md` and optional
`references/`, `scripts/`, `assets/`, `evals/` subtrees.

Interfaces (all paths confined to the skill folder via `resolve_in_root`):

```python
class SkillStore:
    def __init__(self, root: Path)
    def _safe(self, name: str) -> Path            # skill dir; rejects name escapes (existing)
    def _in_skill(self, name: str, rel: str) -> Path  # resolve_in_root(skill_dir, rel); default "SKILL.md"
    def list(self) -> list[tuple[str, str]]        # (name, description) from frontmatter (existing)
    def read(self, name: str, rel: str = "SKILL.md") -> str
    def write(self, name: str, content: str, rel: str = "SKILL.md") -> None
    def files(self, name: str) -> list[str]        # sorted relative paths of all files in the skill
```

`write(..., rel="SKILL.md")` keeps the existing frontmatter-synthesis behavior
(wrap with `name`/`description` when missing). For any other `rel`, content is
written verbatim. A helper `validate_description(desc) -> str|None` returns an
error string if the description exceeds **1024 chars** (empty allowed at write
time but surfaced in the Skills UI).

Tools (all `mutating=False` — authoring files is not a gated action):

- `read_skill(name, path="SKILL.md")` — read any file in the skill.
- `write_skill(name, content, path="SKILL.md")` — write any file in the skill.
  Description now documents references/scripts/assets/evals layout; rejects a
  `description:` over 1024 chars for `SKILL.md` writes.
- `skill_files(name)` — list the skill's file tree.

### 2. Encoded methodology — bundled `creating-skills` skill (new files)

`skills/creating-skills/`:
- `SKILL.md` — concise core: the lifecycle, the SKILL.md format, and
  "read `references/<x>.md` when …" progressive-disclosure pointers.
- `references/best-practices.md` — scoping, spend-context-wisely, Gotchas,
  templates, checklists, validation loops, plan-validate-execute, calibrate control.
- `references/descriptions.md` — imperative phrasing, user-intent, be-pushy,
  1024-char limit, trigger-eval design, train/val split, the optimization loop.
- `references/evals.md` — test-case design, with/without baseline, assertions,
  grading principles, benchmark deltas, iteration loop.
- `references/scripts.md` — one-off vs bundled, self-contained deps, agent-friendly
  script design (no interactive prompts, `--help`, structured stdout/stderr, exit
  codes, dry-run), and the AON-specific note that scripts run via the **gated**
  `run_command`.
- `evals/evals.json` — a small sample suite so the format is self-documenting.

### 3. Instrumentation — token/timing capture (`src/agent/llm_client.py`, modify)

Add `usage: dict | None` to `ChatResult`, populated from the OpenAI-compat
response's top-level `usage` object. Non-breaking (defaults `None`).

`skill_evals.py` provides `InstrumentedLLM(inner)` that delegates `chat`/
`list_models`, accumulates `total_tokens` and call count, and records wall time —
so the eval harness measures cost without touching `engine.py`.

### 4. Eval harness (`src/agent/skill_evals.py`, new)

```python
@dataclass
class EvalCase:
    id: str
    prompt: str
    expected_output: str = ""
    files: list[str] = []            # relative to the skill's evals/ dir
    assertions: list[str] = []

@dataclass
class TriggerQuery:
    query: str
    should_trigger: bool

def load_suite(store, name) -> tuple[list[EvalCase], list[TriggerQuery]]
    # parse skills/<name>/evals/evals.json
```

Runner (async), parameterized by an `agent_factory(workspace: Path, skills_dir: Path|None, model: str)`:

- `run_once(case, with_skill: bool) -> RunResult + outputs + usage + ms`
  1. `ws = mkdtemp()`; copy `case.files` into `ws`.
  2. Build skills dir: a temp dir containing **only** the target skill (with_skill)
     or **empty** (without_skill). Copy the skill folder in for with_skill.
  3. Build agent via `agent_factory(ws, skills_dir, model)` with
     `AutoGate(approve=True)` and an `InstrumentedLLM`.
  4. `await agent.run(case.prompt)`; collect files written under `ws` as outputs.
  5. `rmtree(ws)` after capturing outputs' text (read into memory).
- `grade(case, outputs_text, llm) -> list[{text, passed, evidence}]` — one LLM
  call returning JSON; robust parse; each assertion gets PASS/FAIL + evidence.
- `trigger_rate(query, runs: int) -> float` — run the agent (with the skill
  available) `runs` times on `query`; a run "triggered" if any step is
  `read_skill(name == target)`. Returns fraction.
- `run_suite(name, output_runs=1, trigger_runs=3) -> dict` — runs all cases
  with/without, grades, runs trigger queries, aggregates:
  ```json
  {
    "skill": "...",
    "cases": [{ "id", "with": {pass_rate, ms, tokens, assertions:[...]},
                "without": {pass_rate, ms, tokens, assertions:[...]} }],
    "triggers": [{ "query", "should_trigger", "trigger_rate", "pass": bool }],
    "summary": { "with": {pass_rate, ms, tokens}, "without": {...},
                 "delta": {pass_rate, ms, tokens},
                 "trigger_pass_rate": 0.0 }
  }
  ```
  Also writes the dict to `skills/<name>/evals/benchmark.json`.

Tool: `RunSkillEvalsTool` → `run_skill_evals(name, output_runs?, trigger_runs?)`,
**`mutating=True`** (you approve launching the sandbox). Output is a short text
summary of the deltas; full report persisted to `benchmark.json`.

Safety recap: sub-run file tools are confined to `ws`; the bounded executor
(SIGKILL on timeout, output caps) applies; guardrail-path writes refused;
`AutoGate` lives only here.

### 5. Web + UI (`src/agent/web.py`, `static/index.html`, modify)

- `GET /api/skills/{name}` → `{name, description, files: [...], has_evals: bool}`.
- `GET /api/skills/{name}/report` → the saved `benchmark.json` (or `{}`).
- `POST /api/skills/{name}/evals/run` → runs `run_suite` (await), returns the
  benchmark dict. (Long-running; the UI shows the loader.)
- **Skills modal** gains, per skill: a file tree, a **Run evals** button, and a
  benchmark view (per-case with/without pass bars, the delta, and trigger-rate
  table). Uses the existing AON loader while running.

---

## Data flow (eval run)

```
UI "Run evals" / agent run_skill_evals(name)
   → run_suite(name)
       for each case:  run_once(with) ‖ run_once(without)  → outputs
                       grade(with)    grade(without)        → assertion PASS/FAIL
       for each trigger query: trigger_rate(runs)
       aggregate → benchmark.json  → report to UI / summary to agent
```

## Error handling

- Missing/invalid `evals.json` → clear error, no run.
- Sub-run exception (model down, timeout) → that case recorded with an `error`
  field and pass_rate 0; the suite continues.
- Grader returns unparseable JSON → retry once, then mark assertions `FAIL` with
  evidence "grader parse error".
- Every temp workspace is removed in a `finally`.

## Testing (TDD)

- `SkillStore` v2: multi-file read/write path-confinement (reject `../` escape),
  `files()` tree, 1024-char description validation. (RED→GREEN)
- `load_suite` parses cases + trigger queries from `evals.json`.
- `trigger_rate` detects a `read_skill` step via a fake LLM that emits a
  `read_skill` tool call; detects non-trigger when it doesn't.
- `grade` parses a fake-LLM JSON verdict into assertion results.
- `run_suite` end-to-end with a fake LLM and a tiny skill → benchmark dict has
  `summary.delta` and `triggers`; temp workspaces cleaned.
- `run_skill_evals` tool is `mutating=True`.
- Web: `GET /api/skills/{name}` and `/report`; `POST …/evals/run` with a fake
  agent factory returns a benchmark.
- Guardrail diff empty across all commits.

## Review focus (inputs the tests above must pin)

- **Path escape in multi-file write** (`path="../../etc/x"`) → refused.
- **Description over 1024 chars** → rejected on SKILL.md write.
- **without-skill isolation** → baseline agent genuinely has no access to the
  target skill (empty skills dir), so the delta is real.
- **Temp workspace cleanup** on both success and sub-run exception.
- **`run_skill_evals` is gated** in the interactive agent (mutating=True), while
  sub-runs inside it auto-approve.
