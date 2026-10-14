# AON Intent Router (deliberation phase) — design

**Date:** 2026-10-06
**Status:** for review
**Builds on:** `~/app` on `.84`, branch `foundation`. Adds a think-first phase in front of the agent loop.

## Problem

A weak local model, handed a flat tool list, mis-routes fuzzy requests: "learn this URL as a skill" → read the wrong skill; "make a skill" → asks what to build; "run the playwright code" → ran `make turbo`. Routing is emergent from a big execution prompt, which weak models get wrong.

## Goal

Add a **deliberation (think-first) phase**: before executing, the LLM reasons about what the user means and what actions are needed, emits a structured plan, and the loop routes on it — ask-if-ambiguous, answer-if-trivial, else execute the plan through the **unchanged approval gate**. This is the explicit "reflect → plan" front of AON's reflect→research→plan→propose→approve→execute loop.

## Decisions (locked with the user)

1. LLM-first: one deliberation LLM call per turn (no hand-written keyword rules in v1).
2. Soft routing: the plan is a directive that drives the gated loop; the model still issues the tool calls. Misclassification yields a wrong hint, never an unapproved action.
3. Direct-answer allowed for pure Q&A (skip the tool loop).
4. **Ask, don't guess.** Whenever the request is unclear, ambiguous, underspecified, or the intent/args are uncertain, deliberation sets `needs_clarification` and asks a focused follow-up question — rather than picking a plan on a guess. Clarity first, action second.

## Invariants

- Guardrails NOT modified (`executor.py`, `approval_gate.py`, `telegram.py`, `GUARDRAIL_PATHS`).
- Approval gate unchanged: every mutating action in a plan still requires approval.
- Deliberation failure (model down, bad JSON) degrades gracefully to the normal loop — never aborts a run.

---

## Architecture

### 1. Router — `src/agent/router.py` (new)

Pure, independently testable:

```python
@dataclass
class Plan:
    understanding: str
    intent: str                 # create_skill_from_url | create_skill | learn_url |
                                # run_evals | recall | remember | capabilities | shell_task | answer
    plan: list                  # [{"tool": str, "args": dict}, ...] anticipated actions (may be empty)
    needs_clarification: bool
    question: str | None
    direct_answer: str | None

async def deliberate(goal, llm, *, tools_summary="", skills_summary="", memory_text="") -> Plan
```

- One LLM call with a strict system prompt: "You are the planner for AON. Read the user request and the agent's capabilities. Decide what they want and the actions to take. Return ONLY JSON: {understanding, intent, plan:[{tool,args}], needs_clarification, question, direct_answer}. Prefer a concrete plan. If a URL should become a skill, plan draft_skill. If the request is a plain question you can answer without tools, put the answer in direct_answer. **If anything is unclear, ambiguous, underspecified, or you are not confident of the intent or the required args (e.g. a missing URL, skill name, or target), set needs_clarification=true and ask ONE focused follow-up question — do NOT guess a plan.** Never invent tools."
- Threshold guidance in the prompt: only produce a `plan` when the intent and its args are concrete and unambiguous; otherwise prefer `needs_clarification`.
- `tools_summary`/`skills_summary` are the same compact inventories already built for capability-awareness; `memory_text` is the recent-memory block.
- Robust JSON parse (reuse the `_extract_json_*` pattern). On any failure return `Plan(understanding="", intent="task", plan=[], needs_clarification=False, question=None, direct_answer=None)` — i.e. fall through to the normal loop.
- Validate: drop any planned action whose `tool` is not a registered tool name (passed in as a set). An empty/invalid plan → normal loop.

### 2. Integration — `src/agent/engine.py` (Agent.run), `src/agent/prompts.py`

`Agent.__init__` gains `router=None` (a callable `async (goal) -> Plan`, or `None` to disable — keeps existing tests working). `Agent.run`, before the loop:

```python
if self.router is not None:
    plan = await self.router(goal)                 # deliberation
    if plan.needs_clarification and plan.question:
        return await self._finish("clarify", plan.question, steps)
    if plan.intent == "answer" and plan.direct_answer:
        return await self._finish("done", plan.direct_answer, steps)
    plan_hint = render_plan_hint(plan)             # "" if no actionable plan
else:
    plan_hint = ""
```

`plan_hint` (a short directive: "Understanding: … Intent: … Do now: draft_skill(url=…). Execute this; ask only if a step fails.") is passed into `build_messages(..., plan_hint=plan_hint)` and rendered near the top of the system prompt. The loop then executes normally — **tool calls, approval gate, repetition guard, recovery all unchanged**. The model follows the hint but can adapt if a step errors.

`build_messages` gains `plan_hint=None`; when set, prepends a "Your plan for this turn:" block. `render_plan_hint(plan)` lives in `router.py`.

New emitted status `clarify` (a `final` event with `status:"clarify"`); the UI renders it like a normal AON reply (it already shows `final.final`), optionally with a subtle "needs input" affordance.

### 3. Wiring — `src/agent/build.py`, `src/agent/main.py`

- `build_agent` / `make_agent` construct a router bound to the agent's own `llm` and the registry's tool names + skill list:
  ```python
  reg = build_registry(cfg)
  tool_names = {t.name for t in reg.list()}
  async def _router(goal):
      return await deliberate(goal, llm,
          tools_summary="\n".join(f"- {t.name}: {firstline(t.description)}" for t in reg.list()),
          skills_summary="\n".join(f"- {n}: {d}" for n, d in skill_store(cfg).list()),
          memory_text=mem_store(cfg).read(),
          valid_tools=tool_names)
  Agent(llm, reg, gate, ..., router=_router)
  ```
- Eval sub-agents (`build_eval_agent`) get **no router** (`router=None`) — evals measure the skill/tools, and deliberation would add nondeterminism and cost to every sandboxed sub-run.

### 4. Settings toggle (optional, small)

A config flag `router_enabled` (default true) and a Settings toggle, so you can turn deliberation off to compare behavior. (If this bloats scope, defer — the `router=None` path already makes it trivially switchable in code.)

---

## Flow

```
user goal
  └─ deliberate(goal)  ── needs_clarification? ──► ask the question (final: clarify)
                         ── direct_answer (Q&A)? ─► answer (final: done)
                         ── else ─► plan_hint ─► normal gated agent loop (execute plan, gate on mutating)
         (LLM down / bad JSON) ─► no hint ─► normal gated agent loop
```

## Error handling

- Deliberation exception / unparseable JSON → fall through to the normal loop (no hint). A run is never aborted by the router.
- A planned action naming an unknown tool is dropped; if the whole plan is invalid, no hint.
- `clarify` with an empty question is treated as no-clarify (fall through).

## Testing (TDD)

- `deliberate`: fake LLM returns a valid plan JSON → parsed Plan; junk → fallback Plan (intent="task", no clarify); unknown-tool action dropped; `needs_clarification` + question parsed; an underspecified request (fake LLM returns `needs_clarification:true` + question) → Plan carries the question and an empty plan; `direct_answer` parsed.
- `render_plan_hint`: produces a non-empty directive naming the tool+args for an actionable plan; "" for empty/answer.
- `Agent.run` with a stub router: needs_clarification → status "clarify", returns the question, **no tool runs**; direct_answer → status "done", returns the answer, no tool runs; actionable plan → the plan_hint reaches the LLM (capture system prompt) and the loop runs normally; `router=None` → unchanged behavior.
- `build_messages(plan_hint=...)` → hint present in system content.
- Guardrail diff empty across all commits.

## Review Focus

- **Gate preserved:** a plan proposing a mutating tool still goes through `gate.request` (no auto-execution). (Agent.run test)
- **Graceful degradation:** deliberation raising / bad JSON → the run still completes via the normal loop. (deliberate + Agent test)
- **No clarify loops:** `needs_clarification` returns a single question and stops — it does not re-enter deliberation in a loop. (Agent test)
- **Underspecified → follow-up:** a request missing a required arg (e.g. "make a skill from a URL" with no URL) triggers `needs_clarification` with a focused question, not a guessed plan. (deliberate test)
- **Eval isolation:** eval sub-agents have no router, so benchmarks stay deterministic and cheap. (build test)
- **Unknown-tool safety:** a hallucinated tool in the plan is dropped, not surfaced as an action. (deliberate test)
