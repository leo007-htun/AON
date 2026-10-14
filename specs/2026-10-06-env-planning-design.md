# AON Environment-Awareness + Multi-Step Planning — design

**Date:** 2026-10-06
**Status:** for review
**Builds on:** `~/app` on `.84`, branch `foundation`. Extends the deliberation→loop path and memory.

## Goal

Stop AON from (a) claiming "I can't" without checking, (b) losing the thread on multi-step jobs, and (c) forgetting what it installed. Three reinforcing fixes: probe-before-conclude, engine-tracked plan execution, and auto-captured install memory.

## Decisions (locked)

1. **Engine-tracked todo:** the deliberation plan becomes persistent state the engine re-injects every turn with progress, advancing on successful tool steps and noting failures — not a one-shot hint.
2. **Install memory = auto-capture + prompt:** the engine auto-records install-shaped successful `run_command`s to episodic memory; a prompt directive reinforces it.
3. Environment probing is **behavioral** (run commands to check), never a hardcoded capability list.

## Invariants

- Guardrails NOT modified (`executor.py`, `approval_gate.py`, `telegram.py`, `GUARDRAIL_PATHS`). Engine/prompts/memory are not guardrails.
- Approval gate unchanged. Plan tracking never bypasses it.
- A run with no router / no plan behaves exactly as today (back-compatible).
- A memory failure during capture never aborts a run.

---

## Architecture

### 1. Engine-tracked plan execution — `src/agent/engine.py`, `src/agent/prompts.py`

`Agent.run` already gets a `Plan` from the router. Change: instead of rendering a one-shot `plan_hint`, hold the plan as **persistent loop state** and inject live progress each turn.

- Build `steps = [render_step(a) for a in plan.plan]` where `render_step({"tool","args"})` → a short human string, e.g. `run_command(command=which node)`. If `plan.plan` is empty but `plan.understanding` is set, no step list (plain hint).
- Track `done = 0` (count of successful tool executions this run) and `last_error` (str|None).
- Each loop iteration, build a **plan-progress block** and pass it to `build_messages(..., plan_progress=...)`:
  ```
  Your plan (work the current step; adapt/re-plan if a step fails):
  [x] run_command(command=which node)
  [>] run_command(command=npm i playwright)
  [ ] run_command(command=node script.js)
  ⚠ last step errored: <last_error>  (re-plan if needed)
  ```
  where steps `[:done]` are `[x]`, step `[done]` is `[>]`, rest `[ ]`.
- After each `dispatch`, update state: if the step ran and `output` has no `error:` prefix and it wasn't rejected → `done += 1`, `last_error = None`; if it errored or was rejected → `last_error = <output>` (don't advance).
- The loop's existing repetition guard and gate are unchanged; the plan block is guidance that keeps the overall goal visible every turn.
- `build_messages` gains `plan_progress=None` (injected as its own block near the top, replacing the single-shot `plan_hint` usage from the router path — `plan_hint` kept for back-compat/no-op).

`render_step(a)` and the progress renderer live in `engine.py` (or `router.py`); pure, unit-tested.

### 2. Install-memory auto-capture — `src/agent/engine.py`, `src/agent/memory_extract.py`

- Pure helper `is_install_command(cmd) -> bool` (in `memory_extract.py` or a small module): matches install-shaped shell — `pip install`, `uv pip install`, `npm i`/`npm install`, `pnpm/yarn add`, `apt-get install`/`apt install`, `go install`, `cargo install`, `npx playwright install`, and download+extract (`curl`/`wget` … `tar`/`unzip`), `make install`.
- In `Agent.run`, after a **successful** `run_command` step (approved, no `error:`), if `is_install_command(args["command"])` and `self.memory`: best-effort `self.memory.add(f"installed via `{cmd}` (cwd {workdir})", type="action", tags="install", source="auto")`. Wrapped — never aborts the run. (The agent's `workdir` isn't on the Agent; include just the command; the cwd is the configured workspace, documented in the prompt.)
- Dedup is handled by `EpisodicMemory.add` (same command → one episode).

### 3. Directives — `src/agent/prompts.py`, `src/agent/router.py`

- `SYSTEM_PROMPT` gains: *"Before concluding you lack a program or capability, VERIFY by running commands — `which <bin>`, list your workspace — you may already have it. Never claim a gap you did not check. After installing a tool, record where it is with `remember`."*
- The deliberation planner `_SYS` gains: *"For install/run/setup tasks, make the FIRST step a verification (e.g. `which <bin>` or listing the workspace) before acting; plan the remaining concrete steps in order."*

---

## Flow (the Playwright case, fixed)

```
"run the playwright script"
 └─ deliberate → plan: [which node, npm i playwright, node script.js]   (step 1 = verify)
     loop turn 1: inject plan ([>] which node) → model runs `which node` → done=1
          turn 2: inject ([x] which node, [>] npm i playwright) → runs install → is_install → remember; done=2
          turn 3: inject ([x][x][>] node script.js) → runs script → final
     (if `npm i` errors: ⚠ note injected → model re-plans, doesn't wander to `make turbo`)
```

## Error handling

- `is_install_command` on odd input → `False` (never raises).
- Memory capture wrapped; failure ignored.
- Empty/!plan → no plan block; unchanged behavior.

## Testing (TDD)

- `render_step` / progress renderer: a 3-step plan with `done=1`, `last_error="boom"` → block has `[x]`, `[>]`, `[ ]` in order and the ⚠ line.
- `Agent.run` plan tracking: stub router returns a 2-step plan; fake LLM calls tool A (ok) then tool B then finishes; capture the injected system prompts across turns → progress advances (`[>]` moves, `[x]` grows); a tool that errors → the next turn's block carries the ⚠ note and does NOT advance.
- `build_messages(plan_progress=...)` → block present; `None` → absent.
- `is_install_command`: true for `pip install x`, `npm i y`, `apt-get install z`, `curl … | tar xz`; false for `ls`, `python app.py`, `git status`.
- Install capture: `Agent.run` with a fake memory + a tool whose step command is `pip install foo` (approved, ok) → memory got an episode tagged `install`; a non-install command → no episode.
- Directives: `SYSTEM_PROMPT` contains "which" + "Never claim"; planner `_SYS` contains the verification-step instruction.
- `router=None` / no plan → behavior unchanged (regression).
- Guardrail diff empty across all commits.

## Review Focus

- **Probe-before-conclude:** the directive is present in both the system prompt and the planner prompt; capability tasks plan a verify step. (prompt tests)
- **Plan completion:** progress advances across turns and a failed step injects a re-plan note instead of silently dropping the plan. (engine test)
- **Install memory:** an install-shaped command is auto-remembered (dedup'd); non-install isn't. (helper + engine tests)
- **Gate intact:** plan tracking never auto-approves; a mutating planned step still hits `gate.request`. (engine test)
- **Back-compat:** no router / empty plan → unchanged. (engine test)
