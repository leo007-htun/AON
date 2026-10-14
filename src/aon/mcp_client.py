from __future__ import annotations
import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class McpServerCfg:
    name: str
    command: str = ""
    args: list = field(default_factory=list)
    enabled: bool = True
    transport: str = "stdio"
    url: str = ""
    headers: dict = field(default_factory=dict)


@dataclass
class McpToolSpec:
    server: str
    name: str
    description: str
    input_schema: dict


@dataclass
class McpPromptSpec:
    server: str
    name: str
    description: str


async def _stdio_connector(cfg):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    stack = AsyncExitStack()
    params = StdioServerParameters(command=cfg.command, args=list(cfg.args))
    read, write = await stack.enter_async_context(stdio_client(params))
    session = await stack.enter_async_context(ClientSession(read, write))
    return session, stack.aclose


async def _http_preflight(cfg, transport=None):
    """One plain `initialize` POST so auth/URL errors surface at once instead of hanging."""
    import httpx
    body = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "aon-preflight", "version": "1"}}}
    headers = {"Accept": "application/json, text/event-stream", **(cfg.headers or {})}
    async with httpx.AsyncClient(timeout=20, transport=transport, follow_redirects=True) as c:
        r = await c.post(cfg.url, json=body, headers=headers)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} from {cfg.url}: {r.text[:200]}")


async def _http_connector(cfg):
    await _http_preflight(cfg)
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    stack = AsyncExitStack()
    ctx = await stack.enter_async_context(
        streamablehttp_client(cfg.url, headers=(cfg.headers or None)))
    read, write = ctx[0], ctx[1]
    session = await stack.enter_async_context(ClientSession(read, write))
    return session, stack.aclose


def _text(result):
    parts = []
    for c in getattr(result, "content", []) or []:
        t = getattr(c, "text", None)
        if t:
            parts.append(t)
    return "\n".join(parts)


class McpManager:
    connect_timeout = 120.0   # first `npx -y` run downloads the package

    def __init__(self, store_path="mcp.json", connector=None):
        self.store_path = Path(store_path)
        self._connector = connector or self._default_connector
        self._servers = self._load()
        self._conns = {}

    async def _default_connector(self, cfg):
        if getattr(cfg, "transport", "stdio") == "http":
            return await _http_connector(cfg)
        return await _stdio_connector(cfg)

    def _load(self):
        try:
            return [McpServerCfg(**d) for d in json.loads(self.store_path.read_text())]
        except Exception:
            return []

    def _save(self):
        self.store_path.write_text(json.dumps([asdict(s) for s in self._servers], indent=2))

    def list_servers(self):
        return list(self._servers)

    def add_server(self, cfg):
        self._servers = [s for s in self._servers if s.name != cfg.name] + [cfg]
        self._save()

    def remove_server(self, name):
        self._servers = [s for s in self._servers if s.name != name]
        self._save()

    def set_enabled(self, name, on):
        for s in self._servers:
            if s.name == name:
                s.enabled = on
        self._save()

    async def connect(self, cfg):
        # A dedicated runner task enters AND exits the MCP connection context, so anyio's
        # task-bound cancel scopes never cross tasks. Requests use the shared session.
        ready = asyncio.Event()
        stop = asyncio.Event()
        slot = {"session": None, "tools": [], "prompts": [], "status": "connecting",
                "stop": stop, "task": None}
        self._conns[cfg.name] = slot

        async def runner():
            try:
                session, aclose = await self._connector(cfg)
            except Exception as e:
                slot["status"] = f"error: {e}"
                ready.set()
                return
            try:
                await session.initialize()
                tools = [McpToolSpec(cfg.name, t.name, getattr(t, "description", "") or "",
                                     getattr(t, "inputSchema", None) or {"type": "object", "properties": {}})
                         for t in (await session.list_tools()).tools]
                try:
                    prompts = [McpPromptSpec(cfg.name, p.name, getattr(p, "description", "") or "")
                               for p in (await session.list_prompts()).prompts]
                except Exception:
                    prompts = []
                slot.update(session=session, tools=tools, prompts=prompts, status="connected")
                ready.set()
                await stop.wait()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                slot["status"] = f"error: {e}"
                ready.set()
            finally:
                ready.set()
                try:
                    res = aclose()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

        slot["task"] = asyncio.create_task(runner())
        try:
            await asyncio.wait_for(ready.wait(), timeout=self.connect_timeout)
        except asyncio.TimeoutError:
            slot["status"] = (f"error: no response within {int(self.connect_timeout)}s "
                              "- check the command and args")
            slot["task"].cancel()

    async def connect_all(self):
        for cfg in self._servers:
            if cfg.enabled:
                await self.connect(cfg)

    async def disconnect(self, name):
        c = self._conns.pop(name, None)
        if not c:
            return
        try:
            if c.get("stop"):
                c["stop"].set()
            if c.get("task"):
                await asyncio.wait_for(c["task"], timeout=5)
        except Exception:
            if c.get("task"):
                c["task"].cancel()

    def status(self):
        out = []
        for s in self._servers:
            c = self._conns.get(s.name, {})
            out.append({"name": s.name, "command": s.command, "args": s.args, "enabled": s.enabled,
                        "transport": s.transport, "url": s.url,
                        "status": c.get("status", "disconnected"),
                        "tools": [t.name for t in c.get("tools", [])],
                        "prompts": [p.name for p in c.get("prompts", [])]})
        return out

    def tools(self):
        return [t for c in self._conns.values() for t in c.get("tools", [])]

    def prompts(self):
        return [p for c in self._conns.values() for p in c.get("prompts", [])]

    async def call_tool(self, server, name, args):
        return _text(await self._conns[server]["session"].call_tool(name, args))

    async def get_prompt(self, server, name, args=None):
        res = await self._conns[server]["session"].get_prompt(name, args or {})
        parts = []
        for m in getattr(res, "messages", []) or []:
            c = getattr(m, "content", None)
            t = getattr(c, "text", None)
            if t:
                parts.append(t)
        return "\n".join(parts)
