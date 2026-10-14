# AON Remote MCP Transport (Streamable HTTP) — design

**Date:** 2026-10-07
**Status:** for review
**Builds on:** `~/app` on `.84`, branch `foundation`. Extends the existing MCP client (`mcp_client.py`, stdio) to also connect by URL.

## Goal

Let AON connect to hosted MCP servers by **URL** over **Streamable HTTP**, with **optional static headers** (empty = no auth; `Authorization: Bearer …` for token/API-key servers). Everything else — the runner-task lifecycle, `McpTool` wrapping, gating, composite prompts, UI shell — is reused unchanged.

## Decisions (locked)

1. Transport: **Streamable HTTP only** (the modern transport GitHub and most 2025+ servers use). SSE and OAuth deferred.
2. Auth: **optional static headers**, stored in `mcp.json`. No-auth = empty headers.
3. Stdio remains the default transport; `transport` is a per-server field.

## Invariants

- Guardrails NOT modified. MCP tools stay **gated** (`mutating=True`), excluded from eval sandboxes.
- A remote server that fails to connect is isolated (status "error"), never fatal.
- Existing stdio servers and all current MCP tests keep working unchanged (`transport` defaults to `stdio`).

## Architecture

### 1. Config — `src/agent/mcp_client.py`

`McpServerCfg` gains:
```python
transport: str = "stdio"     # "stdio" | "http"
url: str = ""                # for transport="http"
headers: dict = field(default_factory=dict)   # optional static headers
```
(Back-compat: existing `mcp.json` rows without these load with defaults — `McpServerCfg(**d)` already tolerates missing keys only if defaulted; `_load` maps unknown/missing safely.)

### 2. HTTP connector — `src/agent/mcp_client.py`

- `_http_connector(cfg)`: uses the SDK's streamable-HTTP client:
  ```python
  from mcp import ClientSession
  from mcp.client.streamable_http import streamablehttp_client
  stack = AsyncExitStack()
  read, write, _ = await stack.enter_async_context(
      streamablehttp_client(cfg.url, headers=cfg.headers or None))
  session = await stack.enter_async_context(ClientSession(read, write))
  return session, stack.aclose
  ```
  (The exact unpack/arg names are isolated here; adjust to the installed `mcp` 1.x API at the live smoke — ledger any ruling.)
- **Default connector dispatches on transport:** the manager's default connector becomes `self._default_connector(cfg)` → `_http_connector` when `cfg.transport == "http"` else `_stdio_connector`. An injected `connector` (tests) still overrides entirely. The runner-task `connect()` is unchanged (it calls `self._connector(cfg)`).

### 3. Web — `src/agent/web.py`

`POST /api/mcp` body accepts `transport`, `url`, `headers` (in addition to `name`/`command`/`args`), building `McpServerCfg(..., transport=…, url=…, headers=…)`. `GET /api/mcp` status already returns per-server fields; add `transport`/`url` to the status dict.

### 4. UI — `static/index.html`

The Add-server form gains a **transport selector** (stdio | http). When `http`: show a **URL** field and an optional **headers** field (one `Key: Value` per line, parsed to a dict); hide command/args. When `stdio`: current command/args fields. The server list shows transport + URL for http servers.

## Flow

```
Add server (http): name=github, url=https://api.githubcopilot.com/mcp/, headers={Authorization: Bearer <pat>}
  └─ connect() → _default_connector → _http_connector(url, headers) → runner task holds session
       tools discovered → registered as gated mcp__github__* → agent can call (with your approval)
```

## Error handling

- Missing `url` for `transport="http"` → connect records `status: "error: url required"`.
- Connect/call failures isolated (existing runner try/except).
- Headers absent → sent as no auth.

## Testing (TDD) — fakes for unit, live smoke for the real path

- `McpServerCfg` round-trips `transport`/`url`/`headers` through `mcp.json` (add/save/reload); old rows (no new keys) still load.
- Default-connector dispatch: a manager with the real default connector picks `_http_connector` for `transport="http"` and `_stdio_connector` otherwise — tested by monkeypatching the two connectors to markers and asserting which is called (no real I/O).
- `POST /api/mcp` with `transport=http,url=…,headers=…` (fake manager) → server stored with those fields; `GET` returns them.
- UI smoke: form has `transport` + `url` markers.
- Guardrail diff empty across commits.
- **Live smoke (post-build):** run a local FastMCP server with `transport="streamable-http"` on a port; add it to AON by `url=http://127.0.0.1:PORT/mcp`; confirm tools list + a call. Validates the real streamable-HTTP path without a hosted endpoint.

## Review Focus

- **Back-compat:** existing stdio servers / `mcp.json` without the new keys still connect; all current MCP tests pass. (cfg + manager tests)
- **Dispatch:** `transport="http"` uses the HTTP connector, `stdio` uses stdio. (dispatch test)
- **Auth optional:** no headers → connects with no auth; headers → sent. (covered by live smoke + cfg test)
- **Gate/eval invariants unchanged:** remote MCP tools are still gated and excluded from eval agents. (unchanged; existing tests)
