---
name: playwright-mcp
description: Enables an LLM-assisted client (VS Code, Cursor, Claude, etc.) to control a headless or headed browser through Playwright’s Model Context Protocol (MCP). Use this skill to install the MCP server, configure the client, navigate pages, click or type elements, capture screenshots, run Playwright scripts, mock network traffic, and manage storage state—all via structured accessibility snapshots.
---

## Prerequisites  
1. **Node.js 20 or newer** – used by the MCP server.  
2. **An MCP client** that supports configuration via JSON (e.g., VS Code, Cursor, Claude Code/ Desktop, Windsurf, Copilot CLI).  

## Installing the MCP Server  
The easiest way is to run the server via `npx`. This installs the latest version automatically.  

**Command**  
```sh
npx @playwright/mcp@latest
```

> The server starts in *headed* mode by default so you can see what the agent is doing.

### Headless mode  
Add `--headless` to the arguments:  
```sh
npx @playwright/mcp@latest --headless
```

### Choosing a browser  
Supported browsers are `chrome`, `firefox`, `webkit`, `msedge`.  
```sh
npx @playwright/mcp@latest --browser=firefox
```

### Custom user‑data directory  
```sh
npx @playwright/mcp@latest --user-data-dir=/path/to/dir
```

## Adding to a Client  

| Client | Add MCP Server Config |
|--------|----------------------|
| **VS Code** | *Via UI:* Press the “Add MCP Server” button in the Playwright sidebar or run: <br>`code --add-mcp '{"name":"playwright","command":"npx","args":["@playwright/mcp@latest"]}'` |
| **Cursor** | Go to *Cursor Settings → MCP → Add new MCP Server*, use *command* `npx @playwright/mcp@latest`. |
| **Claude Code** | `claude mcp add playwright npx @playwright/mcp@latest` |
| **Claude Desktop** | Follow the generic MCP install guide and paste the JSON config into the client’s setting panel. |
| **Other clients** | Place the following JSON in the client’s MCP configuration file (most use a similar structure). |
```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": [
        "@playwright/mcp@latest"
      ]
    }
  }
}
```

> **Tip** – If you need the server to listen via HTTP (e.g., no display or from an IDE worker), start it with a port:  
> `npx @playwright/mcp@latest --port 8931`  
> Then configure the client with `url: "http://localhost:8931/mcp"`.

## Core Usage Patterns  

1. **Navigate**  
   Ask: `"Go to https://demo.playwright.dev/todomvc"`  
2. **Click**  
   Ask: `"Click the Submit button"` – the assistant uses the snapshot ref.  
3. **Fill a form**  
   Ask: `"Fill in the email field with test@example.com"`  
4. **Take a screenshot**  
   Ask: `"Take a screenshot of the page"` or `"Take a screenshot of the Submit button"`  
5. **Run Playwright code** (dangerous, see Gotchas)  
   Ask: `"Run this Playwright code: async (page) => { const count = await page.getByTestId('todo-count').textContent(); return count; }"`  
6. **Mock an API**  
   Ask: `"Mock the /api/users endpoint to return { user: 'Bob' }"`  
7. **Network inspection**  
   Ask: `"Show me the last 10 network requests"`  
8. **Storage state**  
   - **Save:** `"Save the current browser state to file 'auth.json'"`  
   - **Restore:** `"Restore state from 'auth.json'"`  
9. **Tabs**  
   Ask: `"Open a new tab and go to https://example.com"`  
10. **Browser console**  
    Ask: `"List console messages from the last 5 minutes"`  

## Advanced Configuration  

* **JSON config file** – run:  
  `npx @playwright/mcp@latest --config path/to/config.json`  
  The config supports context options, timeouts, network rules, etc. See the Playwright MCP repo for the schema.

* **Timeout** – If using an HTTP session and your client doesn’t respond to server‑initiated pings, set:  
  `export PLAYWRIGHT_MCP_PING_TIMEOUT_MS=120000`  

## Gotchas & Safety  

| Issue | Recommendation |
|-------|----------------|
| **RCE risk** | Only use `browser_run_code_unsafe` if you fully trust the client or the user. Disable it for open‑to‑any clients. |
| **Headless vs Headed** | Headed mode is the default; headless may hide visual debugging. Set `--headless` explicitly if you want no UI. |
| **Browser cache** | Persistent mode creates a profile per workspace hash. If you need the same auth across projects, set `--user-data-dir` to a shared folder. |
| **No display** | On headless servers (e.g., CI, containers) use the HTTP server mode (`--port`) to avoid X‑server requirements. |
| **Ping timeout** | Long pauses in the client can cause the server to terminate the session. Adjust `PLAYWRIGHT_MCP_PING_TIMEOUT_MS` accordingly. |
| **Network mocking** | Be careful with request interception; misconfigured patterns can break site functionality. Verify with “View network requests” first. |
| **Security of HTTP transport** | The HTTP endpoint is unencrypted by default. Use a reverse proxy or VPN if exposing over a network. |

## Quick Reference Matrix  

| Action | Sample Prompt | MCP Tool | Notes |
|--------|---------------|-----------|-------|
| Open URL | `Navigate to https://example.com` | navigation | |
| Click element | `Click the Sign‑in button` | click | Uses snapshot ref |
| Type text | `Fill the username field with alice` | type | |
| Screenshot | `Take a screenshot of the modal` | screenshot | |
| Run code | `Run this Playwright code: async (page)=>{…}` | browser_run_code_unsafe | Requires trusted client |
| Mock API | `Mock /api/data to return [{"id":1}]` | mock_route | |
| Inspect network | `Show all network requests since load` | view_network | |
| Restore state | `Restore state from 'cookie.json'` | restore_state | |

---  
Use this skill as a repeatable building block for any LLM‑driven browsing workflow that requires reliable, headless automation without visual models.