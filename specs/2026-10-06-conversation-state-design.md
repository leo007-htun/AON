# AON Conversation State (multi-turn) — design

**Date:** 2026-10-06
**Status:** for review
**Builds on:** `~/app` on `.84`, branch `foundation`. Makes runs multi-turn so clarify follow-ups connect.

## Problem

Each message is an independent run: the UI opens a new WebSocket per goal, `Agent.run` starts with empty history, and nothing carries between goals. So the deliberation clarify branch ("Which URL?") is orphaned — the user's next message has no link to the question.

## Goal

Carry the recent conversation (prior user + assistant turns) into each run — into **both the deliberation pass and the agent loop** — using a server-side relational store, so follow-ups (clarify answers, "yes", "that one") resolve in context. Conversation state is recency-ordered and relational — **not** RAG/vector; long-term semantic memory stays the separate `episodes.db` layer.

## Decisions (locked with the user)

1. **Server-authoritative**, stored in the existing `app.db` (`persistence.Database`) — a new `conversations` table. No new DB engine; long-term memory stays in `episodes.db`.
2. **Bounded window:** the last ~10 turns are loaded per run (keeps the prompt lean for weak local models).
3. Session identity = the UI's existing per-chat `session_id`; new chat → new id → fresh context.

## Invariants

- Guardrails NOT modified (`executor.py`, `approval_gate.py`, `telegram.py`, `GUARDRAIL_PATHS`).
- Approval gate unchanged.
- A conversation-store failure must not abort a run (best-effort load/save, wrapped).
- `Agent.run(goal)` with no conversation keeps current behavior (back-compatible default).

---

## Architecture

### 1. Store — `src/agent/persistence.py` (add a table + methods)

Add to the schema:
```sql
CREATE TABLE IF NOT EXISTS conversations(
  id INTEGER PRIMARY KEY AUTOINCREMENT, convo TEXT, role TEXT, content TEXT, created_at REAL);
CREATE INDEX IF NOT EXISTS idx_convo ON conversations(convo, id);
```
Methods on `Database`:
- `add_turn(convo: str, role: str, content: str) -> int`
- `recent_turns(convo: str, limit: int = 10) -> list[dict]` — the last `limit` turns for `convo`, returned **oldest-first** as `{"role","content"}` (ready to prepend to messages).
- `clear_conversation(convo: str) -> None` (for a future "reset this chat" / privacy).

### 2. Deliberation + loop consume conversation — `src/agent/router.py`, `src/agent/engine.py`, `src/agent/prompts.py`

- `deliberate(goal, llm, *, tools_summary="", skills_summary="", memory_text="", valid_tools=None, conversation=None)`: when `conversation` is given, prepend a compact transcript ("Recent conversation:\n<role>: <content>…") to the planner's user message so references resolve. (Bounded: already the last ~10 turns.)
- `Agent.run(goal, conversation=None)`: seed the loop's `history` with the prior turns as `{"role","content"}` messages **before** the first model call, so the executing model also sees the thread. The router closure is called as `self.router(goal, conversation)`.
- `Agent.__init__` unchanged except the router is now invoked with two args; `render_plan_hint` unchanged.
- `build_messages` already accepts `history`; the seeded conversation flows in via the loop's `history` list (no signature change). Prior turns carry only `role`/`content` (no tool_calls), so they render as plain context.

**Router signature update:** `make_router` returns `async def _router(goal, conversation=None)`, forwarding `conversation` to `deliberate`. `Agent.run` calls `await self.router(goal, conversation)`. (Routers that ignore the 2nd arg still work; the stub routers in tests accept `*a`.)

### 3. Wiring — `src/agent/web.py`, `src/agent/main.py`

- The `/ws` goal message gains an optional `session_id` (string). On receipt:
  1. `convo = session_id or "default"`.
  2. `conversation = db.recent_turns(convo, 10)` (best-effort; `[]` on error).
  3. `db.add_turn(convo, "user", goal)` (best-effort).
  4. `make_agent(gate, on_event, model)` → `agent.run(goal, conversation=conversation)`.
  5. After the run, `db.add_turn(convo, "assistant", res.final)` (best-effort).
- `make_agent` passes `conversation` through to `agent.run`. The existing `run_agent` task already captures `res`; extend it to pass `conversation` and save the assistant turn.
- `db` is the app's `Database` (already in `create_app`).

### 4. Frontend — `static/index.html`

- `go()` includes `session_id: curId` in the WS `goal` message. (`curId` already exists.)
- No other change: server owns the history. New chat already mints a new `curId` → fresh server context.

---

## Flow

```
UI go(goal, session_id=curId)
  └─ /ws: convo=session_id
          conversation = db.recent_turns(convo, 10)
          db.add_turn(convo,"user",goal)
          agent.run(goal, conversation)
              ├─ deliberate(goal, …, conversation)  → clarify/answer/plan with context
              └─ loop seeded with prior turns → gated execution
          db.add_turn(convo,"assistant",final)
```

## Error handling

- Any `conversations` read/write wrapped — failure logs nothing fatal; the run proceeds with `conversation=[]` (degrades to single-turn, never aborts).
- Missing `session_id` → `convo="default"` (still works; shared bucket).

## Testing (TDD)

- `Database`: `add_turn` then `recent_turns` returns oldest-first `{role,content}`; `recent_turns` caps at `limit`; `clear_conversation` empties it; unknown convo → `[]`.
- `deliberate(conversation=...)`: the transcript reaches the planner (capture the fake LLM's user message); no conversation → unchanged.
- `Agent.run(goal, conversation=[...])`: prior turns appear in the messages the model sees (capture system+history); `router` receives the conversation; `conversation=None` → unchanged.
- `web /ws`: a two-message exchange on the same `session_id` — the second run's agent sees the first turn (via a fake make_agent that records the `conversation` it was given); turns are persisted.
- Guardrail diff empty across all commits.

## Review Focus

- **Continuity:** a clarify question in turn 1 is present in turn 2's conversation → the follow-up resolves with context. (web test)
- **Isolation:** two different `session_id`s do not see each other's turns. (Database test)
- **Degradation:** a conversation-store failure does not abort the run (falls back to single-turn). (web/Agent test)
- **Back-compat:** `Agent.run(goal)` and a router ignoring the 2nd arg behave exactly as before. (engine test)
- **Bound:** only the last `limit` turns are loaded (no unbounded prompt growth). (Database test)
