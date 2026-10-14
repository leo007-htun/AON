# AON Episodic Memory + Learn-from-URL — design

**Date:** 2026-10-06
**Status:** for review
**Builds on:** `~/app` on `.84`, branch `foundation`. Replaces the flat `MemoryStore` (memory.md) with structured episodic memory.

## Problem

AON stores every `remember` as a flat line in `memory.md` and injects the **entire file** into every prompt. Stale test facts ("make turbo", stored 3×) contaminated an unrelated Playwright request. This is the episodic-memory anti-pattern set: store-everything, no supersession, no recency, no dedup, memory-invisible-to-user.

## Goal

Replace it with episodic memory following agentskills/skilldb episodic-memory patterns: structured, selective, recency-weighted retrieval (not blanket injection), supersession to avoid stale contamination, LLM-mediated extraction, learn-from-URL, and user-visible curation. Single-user local box; SQLite + recency/keyword retrieval (no embeddings yet).

## Decisions (locked with the user)

1. Storage: **SQLite, structured**; retrieval by recency + keyword + a `search_memory` tool. Vector embeddings deferred.
2. Extraction: **manual + heuristic + LLM-mediated** (auto-extraction after each run) — included now.
3. **Learn-from-URL** (`learn_url`) included: fetch → LLM-digest → episodes and/or a proposed skill.
4. **Capability self-awareness** included: AON knows its tool inventory, checks skill prerequisites before claiming it can run, and answers "can you do X?" honestly. Running Playwright is **shell-driven** via the existing gated `run_command` (install Node/Playwright, run the script) — no bespoke browser tool.

## Invariants

- Guardrails NOT modified (`executor.py`, `approval_gate.py`, `telegram.py`, `GUARDRAIL_PATHS`).
- Interactive gate unchanged. `remember`, `search_memory`, `learn_url` are read-only/non-gated authoring of memory (they cannot execute shell or write code); `learn_url` performs a web fetch (same trust as the existing `web_fetch`).
- Episodes are per-install (single user); no cross-user concern here.

---

## Architecture

### 1. Episode store — `src/agent/memory.py` (rewrite)

SQLite at `memory/episodes.db`. One table:

```sql
CREATE TABLE IF NOT EXISTS episodes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT,                 -- preference|decision|fact|milestone|action|reference
  content TEXT,
  tags TEXT,                 -- space-separated
  source TEXT,               -- manual|heuristic|llm|url:<domain>
  confidence REAL DEFAULT 1.0,
  active INTEGER DEFAULT 1,
  superseded_by INTEGER,
  created_at REAL,
  last_used_at REAL
);
```

`EpisodicMemory(path)`:
- `add(content, type="fact", tags="", source="manual", confidence=1.0, supersedes=None) -> int`
  - **Dedup:** if an active episode has identical normalized `content`, bump its `created_at`/`confidence` and return its id instead of inserting a duplicate.
  - **Supersede:** if `supersedes` given (or an active episode shares the same `type`+primary tag and the content updates it), set the old row `active=0, superseded_by=<new id>`.
- `recent(limit=8) -> list[Episode]` — active episodes, newest first (what gets injected).
- `search(query, limit=8) -> list[Episode]` — active episodes ranked by keyword overlap (content+tags) then recency; marks returned rows `last_used_at=now`.
- `all(include_inactive=False)`, `get(id)`, `delete(id)`, `mark_outdated(id)` (set `active=0`).
- `as_prompt_text(limit=8) -> str` — compact render of `recent()` for injection (`"- [type] content"`), or `""` when empty.
- **Back-compat shim:** keep a `read()` method returning `as_prompt_text()` and an `append(fact)` returning `add(fact)`, so existing callers/tests keep working during migration.

### 2. Retrieval change — `src/agent/prompts.py`, `src/agent/engine.py`

- `build_messages(..., memory_text=...)` still takes `memory_text`, but callers pass `memory.as_prompt_text(limit=8)` (recency-bounded) instead of the whole file. The injected block is capped and labeled "Recent memory (use search_memory for more):".
- `Agent.run` already calls `self.memory.read()`; with the shim, `read()` returns the bounded recent text. No engine signature change required.

### 3. Tools — `src/agent/memory.py`

- `RememberTool` → `remember(content, type="fact", tags="")` (non-mutating). Structured; dedups/supersedes.
- `SearchMemoryTool` → `search_memory(query)` (non-mutating) — returns matching episodes so the agent pulls relevant older memory on demand (retrieval-as-a-tool).
- `LearnUrlTool` → `learn_url(url)` (non-mutating; performs a web fetch like `web_fetch`): fetch the page text, run the digest (section 4), store resulting episodes, and — for procedural content — return a suggestion to author a skill (the agent then calls `write_skill`). Returns a summary of what was learned (counts by type) so the user sees it.

### 4. LLM extraction / digest — `src/agent/memory_extract.py` (new)

- `extract_episodes(text, llm, context="conversation") -> list[dict]` (async): one LLM call with a strict prompt — "extract only durable, reusable items worth remembering (preferences, decisions, facts, milestones, reference knowledge). Return a JSON array of {type, content, tags}. Skip trivia and anything transient." Robust JSON parse (reuse the `_extract_json_array` pattern), returns [] on failure. **Selectivity lives here** — the prompt instructs the model to store the signal, not the volume.
- `classify_for_skill(text) heuristic`: if the digest yields mostly procedural/how-to content, `learn_url` flags it as skill-worthy in its result.
- **Auto-extraction hook:** after a run finishes (`Agent._finish` or the web layer after `agent.run`), call `extract_episodes` over the session's user+assistant turns and `add` the results with `source="llm"`. To avoid touching guardrails/engine signatures, wire this in the **web layer** (`run_agent` finally-block) and in `build_agent`'s caller, not inside `engine.py`. (Ruling candidate: keep engine clean; extraction is an outer concern.)

### 5. Web + UI — `src/agent/web.py`, `static/index.html`

- `GET /api/memory` → `{episodes: [{id,type,content,tags,created_at,active}], }` (active only by default; `?all=1` includes superseded).
- `DELETE /api/memory` → clear all. `DELETE /api/memory/{id}` → delete one. `POST /api/memory/{id}/outdated` → mark inactive.
- **Memory modal**: list episodes (type chip, content, tags, age) each with **Delete** and **Mark outdated**; a **Clear all** button; shows superseded/inactive dimmed under a toggle. This is the doc's user-visible curation requirement.

### 6. Migration

`memory/memory.md` is already emptied. On first `EpisodicMemory` init, if `memory/memory.md` has non-empty lines, import each as an episode (`type="fact", source="manual"`) once, then rename it `memory.md.imported`. (No-op now since it's empty; keeps the path for anyone with old data.)

### 7. Capability self-awareness — `src/agent/prompts.py`, `src/agent/engine.py`, `src/agent/skills.py`

The playwright failure had two causes beyond memory: AON didn't reason about what it *can* do, and couldn't tell whether a learned skill was *runnable* with its tools. Fixes:

- **Tool inventory in the system prompt.** `build_messages(..., tool_summary=None)` renders a compact list of the agent's tools (name — one-line description) under "Your capabilities (the ONLY actions you can take):", with the directive: *"Map the request to these tools. If none fits, say plainly you cannot do it and what tool/capability would be needed — do not improvise unrelated commands."* `Agent.run` builds the summary from `self.registry` and passes it. (Small change to `engine.py`, which is NOT a guardrail.)
- **Skill prerequisites.** `SKILL.md` frontmatter may include `requires: <space-separated binaries/tools>` (e.g. `requires: node npx`). `SkillStore.list`/`files` already expose frontmatter; skill detail surfaces `requires`. The `creating-skills` methodology documents it. The prompt instructs AON: *before claiming a skill is runnable, verify its prerequisites (e.g. `which node`) and offer to install what's missing.*
- **`capabilities` self-check tool** (non-mutating): returns the current tool inventory + known skills (name, description, requires). So "what can you do?" / "can you do X?" are answered from fact, not guesswork. Registered in `build_registry`.

Net effect on the playwright case: clean memory (no "make turbo") + explicit capabilities → AON recognizes it has `run_command`, can `learn_url` the Playwright docs into a skill that declares `requires: node`, checks/install Node, and runs the script via shell — all gated — instead of flailing.

---

## How "apply or not" is decided (two gates)

- **Digest time:** `extract_episodes` / `learn_url` store only durable, relevant items (selectivity prompt); procedural content becomes a skill, facts become episodes.
- **Use time:** only `recent(8)` active episodes are injected; everything else is reached via `search_memory`; skills activate via description match. Stale or off-topic memory never rides along with an unrelated task.

## Error handling

- Malformed LLM digest → `[]` (store nothing), never crash a run.
- `learn_url` on an unreachable/huge page → bounded fetch, clear error, store nothing.
- All DB ops wrapped; a memory failure must not abort the agent run.

## Testing (TDD)

- `EpisodicMemory`: add+dedup (same content once), supersede (old inactive), recent (bounded, newest first), search (keyword rank + last_used), delete/mark_outdated, as_prompt_text cap. Back-compat `read()/append()`.
- `extract_episodes`: fake LLM returns JSON → parsed episodes; junk → [].
- `remember`/`search_memory`/`learn_url` tools: non-mutating; learn_url with a fake fetcher+LLM stores episodes and reports skill-worthiness.
- Retrieval bound: with 20 episodes, injected prompt text contains only the recent K.
- Web: GET/DELETE/outdated endpoints; UI smoke markers.
- Guardrail diff empty across all commits.

## Review Focus

- **Stale contamination:** an old off-topic episode is NOT injected when 8+ newer ones exist (recency bound). (store test)
- **Dedup:** the same fact remembered 3× yields ONE active episode. (store test)
- **Supersession:** a new preference marks the old one inactive; retrieval skips inactive. (store test)
- **Digest selectivity:** trivial content yields no episodes; a bad LLM response stores nothing and doesn't crash. (extract test)
- **Memory failure isolation:** a DB/extraction error does not abort `agent.run`. (web/engine test)
- **learn_url routing:** procedural content is flagged skill-worthy; factual content stored as episodes. (tool test)
- **Capability honesty:** the tool-inventory appears in the system prompt and names the real tools; `capabilities` returns tools + skills. (prompts + tool tests)
- **Skill prerequisites:** a skill's `requires` frontmatter is parsed and surfaced in skill detail. (skills + web test)
