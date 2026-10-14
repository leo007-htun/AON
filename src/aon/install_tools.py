"""Gated tools that change what AON can do: register an MCP server, import skills from a repo."""
from __future__ import annotations
import re
from aon.tools.base import Tool, ToolResult

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class AddMcpServerTool(Tool):
    name = "add_mcp_server"
    description = (
        "Register and connect an MCP server in AON (same as the MCP servers panel). stdio: give "
        "command + args (array). http: give transport='http', url and optional headers. Returns the "
        "connection status and tool list, or the error.")
    parameters = {"type": "object", "properties": {
        "name": {"type": "string", "description": "short id, e.g. playwright"},
        "transport": {"type": "string", "enum": ["stdio", "http"]},
        "command": {"type": "string"},
        "args": {"type": "array", "items": {"type": "string"}},
        "url": {"type": "string"},
        "headers": {"type": "object", "additionalProperties": {"type": "string"}}},
        "required": ["name"]}
    mutating = True

    def __init__(self, manager):
        self.manager = manager

    async def run(self, args):
        from aon.mcp_client import McpServerCfg
        name = (args.get("name") or "").strip()
        transport = args.get("transport") or ("http" if args.get("url") and not args.get("command") else "stdio")
        if not _NAME.match(name):
            return ToolResult(ok=False, output="", error=f"invalid server name {name!r}")
        if transport == "stdio" and not args.get("command"):
            return ToolResult(ok=False, output="", error="stdio servers need a command")
        if transport == "http" and not args.get("url"):
            return ToolResult(ok=False, output="", error="http servers need a url")
        argv = [str(a) for a in (args.get("args") or [])]
        if args.get("command", "").rstrip("/").endswith("npx") and not {"-y", "--yes"} & set(argv):
            argv = ["-y"] + argv        # npx would otherwise wait on an "Ok to proceed?" prompt
        cfg = McpServerCfg(name, args.get("command", ""), argv,
                           True, transport=transport, url=args.get("url", ""),
                           headers=dict(args.get("headers") or {}))
        self.manager.add_server(cfg)
        try:
            await self.manager.connect(cfg)
        except Exception as e:
            return ToolResult(ok=False, output="", error=f"connect failed: {e}")
        st = next((s for s in self.manager.status() if s["name"] == name), {})
        status = st.get("status", "unknown")
        if status != "connected":
            return ToolResult(ok=False, output="", error=f"server '{name}' added but not connected: {status}")
        tools = ", ".join(st.get("tools") or []) or "(none)"
        return ToolResult(ok=True, output=f"MCP server '{name}' connected. Tools: {tools}. "
                                          "They are available from the next message.")


class ImportSkillsTool(Tool):
    name = "import_skills"
    description = ("Import Agent Skills (folders with SKILL.md) from a GitHub repository URL into AON. "
                   "Optional names= to pick some, overwrite=true to replace installed ones.")
    parameters = {"type": "object", "properties": {
        "url": {"type": "string"},
        "names": {"type": "array", "items": {"type": "string"}},
        "overwrite": {"type": "boolean"}}, "required": ["url"]}
    mutating = True

    def __init__(self, store):
        self.store = store

    async def run(self, args):
        from aon import skill_import as si
        try:
            res = await si.import_from_github(self.store, args["url"], names=args.get("names"),
                                              overwrite=bool(args.get("overwrite")))
        except Exception as e:
            return ToolResult(ok=False, output="", error=str(e))
        skipped = "; ".join(f"{s['name']} ({s['reason']})" for s in res["skipped"]) or "none"
        return ToolResult(ok=bool(res["installed"]) or not res["skipped"],
                          output=f"installed: {', '.join(res['installed']) or 'none'}; skipped: {skipped}")
