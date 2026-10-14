# AON MCP Integration + Conversation Search — design

**Date:** 2026-10-06
**Status:** for review
**Builds on:** `~/app` on `.84`, branch `foundation`.

## Goal

1. **`search_conversation`** tool so AON reaches past the ~10-turn window by searching its full transcript on demand (pairs with `search_memory`).
2. **MCP client**: AON connects to local **stdio** MCP servers, registers their **tools** as gated AON tools and their **prompts** as AON skills, and lets the user **add/remove/enable servers from the UI**.

Graph/temporal long-term retrieval and remote (SSE/HTTP) MCP transport are explicitly out of scope (separate later projects).

## Decisions (locked with the user)

1. **MCP tools are gated by default** (`mutating=True`) — an external server's tool is untrusted and can do anything.
2. **Transport: stdio only** (AON launches `command args`, e.g. `npx @playwright/mcp`, and speaks MCP over its stdio).
3. **Both surfaces:** MCP **tools → Tools list**; MCP **prompts → Skills list**.
4. `search_conversation` included.

## Invariants

- Guardrails NOT modified (`executor.py`, `approval_gate.py`, `telegram.py`, `GUARDRAIL_PATHS`).
- The approval gate is the *only* thing standing between an MCP tool and execution; MCP tools are gated.
- An MCP server that fails to start / disconnects must never crash the app or a run — it's skipped, surfaced as "error" in the UI.
- Adding a server stores a **command the app will execute** — this is user-authorized configuration (like any agent's MCP config); documented as such.

## New dependency

- Python `mcp` SDK (`pip install mcp`) in the venv.
- stdio servers need their launcher on PATH (e.g. `node`/`npx`). AON's workspace Node can be referenced by absolute path in a server's `command`.

---

## Architecture

### 1. `search_conversation` — `src/agent/memory_extract.py` (or a small module), wired per-run

```python
class SearchConversationTool(Tool):
    name = "search_conversation"
    description = "Search THIS chat's earlier turns (beyond the recent window) for a keyword/topic."
    mutating = False
    def __init__(self, db, convo): self.db = db; self.convo = convo
    async def run(self, args):  # keyword search over db.conversation turns for self.convo
        ...
```
- `Database.search_turns(convo, query, limit=10) -> list[{role,content}]` (keyword match, newest-first) added to `persistence.py`.
- Registered **per-run** in `make_agent` (which knows the session's `convo`), so it's scoped to the current chat. `make_agent` gains a `convo` argument; the `/ws` handler passes `session_id`.

### 2. MCP manager — `src/agent/mcp_client.py` (new)

App-level, async, holds connections:

```python
@dataclass
class McpServerCfg: name: str; command: str; args: list; enabled: bool = True

@dataclass
class McpToolSpec: server: str; name: str; description: str; input_schema: dict
@dataclass
class McpPromptSpec: server: str; name: str; description: str; arguments: list

class McpManager:
    def __init__(self, store_path): ...            # loads mcp.json
    async def connect_all(self): ...               # start enabled servers (best-effort)
    async def connect(self, cfg): ...              # spawn stdio server, handshake, list tools+prompts
    async def disconnect(self, name): ...
    def status(self) -> list[dict]: ...            # per server: name, connected|error, tools, prompts
    def tools(self) -> list[McpToolSpec]: ...      # across connected servers
    def prompts(self) -> list[McpPromptSpec]: ...
    async def call_tool(self, server, name, args) -> str: ...   # tools/call -> text result
    async def get_prompt(self, server, name, args=None) -> str: ...  # prompts/get -> text
    # config mgmt
    def list_servers(self); def add_server(self, cfg); def remove_server(self, name); def set_enabled(self, name, on)
```
- Config persisted to `mcp.json` (app root). Connections are best-effort; a server that errors is recorded with its error, not raised.
- Uses the `mcp` SDK's stdio client. One `McpManager` instance lives on `app.state` (created in `create_app`), started on FastAPI startup.
- **Injectability for tests:** `create_app(..., mcp_manager=None)` accepts a manager (a fake in tests) so nothing spawns real subprocesses under pytest.

### 3. MCP tools → gated AON tools — `src/agent/mcp_tools.py` (new) + `build.py`

```python
class McpTool(Tool):
    mutating = True   # gated by default
    def __init__(self, manager, spec: McpToolSpec):
        self.name = f"mcp__{spec.server}__{spec.name}"
        self.description = spec.description
        self.parameters = spec.input_schema or {"type":"object","properties":{}}
        self._mgr = manager; self._spec = spec
    async def run(self, args):
        try: return ToolResult(ok=True, output=await self._mgr.call_tool(self._spec.server, self._spec.name, args))
        except Exception as e: return ToolResult(ok=False, output="", error=str(e))
```
- `build_registry(cfg, ..., mcp_manager=None)`: when a manager is passed, register an `McpTool` for each `manager.tools()`. (Interactive `make_agent`/`build_agent` pass the app's manager; eval agents pass `None`.)

### 4. MCP prompts → Skills — `src/agent/skills.py` or merge in web/build

- A read-only **composite skill list**: file skills (`SkillStore.list()`) **+** MCP prompts (`manager.prompts()` rendered as `(f"mcp:{server}:{name}", description)`).
- `build_messages` `skill_list` and `/api/skills` use the composite list, so the model and UI both see MCP prompts as skills.
- `read_skill("mcp:server:name")` returns `await manager.get_prompt(server, name)` (the expanded prompt text). (`ReadSkillTool` gains an MCP branch, or a small `CompositeSkills` wrapper provides `list()/read()`.)

### 5. Web + UI — `src/agent/web.py`, `static/index.html`

Endpoints:
- `GET /api/mcp` → `{servers:[{name,command,args,enabled,status,tools:[...],prompts:[...]}]}`.
- `POST /api/mcp` body `{name,command,args,enabled}` → add + connect; returns status.
- `DELETE /api/mcp/{name}` → disconnect + remove.
- `POST /api/mcp/{name}/toggle` → enable/disable (connect/disconnect).
- `/api/skills` returns the composite list (file skills + MCP prompts), each tagged `source: "file"|"mcp"`.

UI: a new **MCP** panel (rail/sidebar) — list servers with status dots, **Add server** form (name, command, args), remove/toggle, and each server's tools + prompts shown. The Tools view (in `capabilities`) and Skills modal reflect MCP entries (tools under Tools, prompts under Skills), tagged as MCP.

### 6. Wiring — `src/agent/main.py`

- `create_app` builds an `McpManager` (unless injected), stores on `app.state.mcp`, and on startup `await mcp.connect_all()`.
- `make_agent(gate, on_event, model, convo="default")`: `reg = build_registry(cfg, mcp_manager=app.state.mcp)`, then `reg.register(SearchConversationTool(db, convo))`; passes the composite skills into the agent's `skills`.
- `/ws` passes `session_id` as `convo` to `make_agent`.

---

## Safety

- MCP tools gated (`mutating=True`) → every call needs approval, shown as `mcp__server__tool` with its args in the approval card.
- MCP tools are **excluded from eval sub-agents** (eval `build_registry` passes `mcp_manager=None`) — deterministic, no external side effects in sandboxes.
- Adding a server runs a user-provided command; this is explicit user configuration. The app never auto-adds servers.
- A misbehaving/hanging server is isolated: `call_tool` is wrapped and bounded; connection errors are surfaced, not fatal.

## Error handling

- `connect`/`connect_all` best-effort; failures recorded as server `status:"error"` with the message.
- `call_tool`/`get_prompt` exceptions → `ToolResult(ok=False)` / skill read error; never abort the run.
- Malformed `mcp.json` → empty server list + a logged note.

## Testing (TDD) — no real subprocesses

- `Database.search_turns`: keyword match within a convo, newest-first, capped, convo-isolated.
- `SearchConversationTool`: returns matching turns for its convo; `(no match)` when none.
- `McpTool`: wraps a **fake manager**; `run` calls `call_tool` and returns its text; is `mutating`; error → `ok=False`. Name is `mcp__server__tool`.
- Composite skills: a fake manager with prompts → `/api/skills` and the skill_list include them tagged `mcp`; `read_skill("mcp:...")` returns the prompt text.
- `build_registry(mcp_manager=fake)` registers an McpTool per fake tool; `mcp_manager=None` registers none; eval agents get none.
- Web: `GET /api/mcp` (fake manager), `POST`/`DELETE`/`toggle` mutate the fake manager; `/api/skills` composite.
- UI smoke: MCP markers (`Add server`, `/api/mcp`) present.
- Guardrail diff empty across all commits.

## Review Focus

- **MCP tools are gated:** an `McpTool` is `mutating=True` and a plan proposing one still hits `gate.request`. (tools + engine)
- **Failure isolation:** a server that errors on connect / a tool that raises does not crash the app or abort the run. (manager + McpTool tests)
- **Eval isolation:** eval sub-agents have no MCP tools. (build test)
- **Composite correctness:** MCP prompts appear as skills (model + UI) and `read_skill` fetches the prompt; file skills still work. (skills/web tests)
- **Conversation reach:** `search_conversation` finds a turn older than the injected window, scoped to the session. (persistence + tool tests)
