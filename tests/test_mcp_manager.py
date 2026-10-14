import json
import pytest
from aon.mcp_client import McpManager, McpServerCfg


class FakeSession:
    async def initialize(self): pass
    async def list_tools(self):
        class T: pass
        t = T(); t.name = "nav"; t.description = "navigate"; t.inputSchema = {"type": "object", "properties": {"url": {"type": "string"}}}
        return type("R", (), {"tools": [t]})()
    async def list_prompts(self):
        class P: pass
        p = P(); p.name = "howto"; p.description = "how to"
        return type("R", (), {"prompts": [p]})()
    async def call_tool(self, name, args):
        txt = type("C", (), {"text": f"called {name} {args}"})()
        return type("R", (), {"content": [txt]})()
    async def get_prompt(self, name, args=None):
        msg = type("M", (), {"content": type("C", (), {"text": "PROMPT BODY"})()})()
        return type("R", (), {"messages": [msg]})()


async def fake_connector(cfg):
    return FakeSession(), (lambda: None)


def _mgr(tmp):
    return McpManager(str(tmp / "mcp.json"), connector=fake_connector)


@pytest.mark.asyncio
async def test_add_connect_status_tools_prompts(tmp_path):
    m = _mgr(tmp_path)
    m.add_server(McpServerCfg("pw", "npx", ["@playwright/mcp"]))
    await m.connect_all()
    st = m.status()
    assert st[0]["name"] == "pw" and st[0]["status"] == "connected"
    assert any(t.name == "nav" for t in m.tools())
    assert any(p.name == "howto" for p in m.prompts())


@pytest.mark.asyncio
async def test_call_tool_and_get_prompt(tmp_path):
    m = _mgr(tmp_path)
    m.add_server(McpServerCfg("pw", "npx", ["x"]))
    await m.connect_all()
    assert "called nav" in await m.call_tool("pw", "nav", {"url": "http://x"})
    assert "PROMPT BODY" in await m.get_prompt("pw", "howto")


@pytest.mark.asyncio
async def test_connect_error_is_isolated(tmp_path):
    async def boom(cfg): raise RuntimeError("spawn failed")
    m = McpManager(str(tmp_path / "mcp.json"), connector=boom)
    m.add_server(McpServerCfg("bad", "nope", []))
    await m.connect_all()
    assert m.status()[0]["status"].startswith("error")
    assert m.tools() == []


def test_config_persist_add_remove(tmp_path):
    p = str(tmp_path / "mcp.json")
    m = McpManager(p)
    m.add_server(McpServerCfg("a", "cmd", ["x"]))
    assert json.load(open(p))[0]["name"] == "a"
    m2 = McpManager(p)
    assert [s.name for s in m2.list_servers()] == ["a"]
    m2.remove_server("a")
    assert m2.list_servers() == []


@pytest.mark.asyncio
async def test_connect_times_out_instead_of_hanging(tmp_path):
    import asyncio
    class HangSession(FakeSession):
        async def initialize(self): await asyncio.sleep(3600)
    async def hang(cfg): return HangSession(), (lambda: None)
    m = McpManager(str(tmp_path / "mcp.json"), connector=hang)
    m.connect_timeout = 0.2
    m.add_server(McpServerCfg("slow", "x", []))
    await asyncio.wait_for(m.connect_all(), timeout=5)
    st = m.status()[0]["status"]
    assert st.startswith("error") and "no response" in st


@pytest.mark.asyncio
async def test_real_failing_command_reports_error(tmp_path):
    # the actual failure seen live: `env` given a bogus program -> process exits at once
    import asyncio
    m = McpManager(str(tmp_path / "mcp.json"))
    m.connect_timeout = 5
    m.add_server(McpServerCfg("bad", "env", ["PATH=/usr/bin:/bin", ":/usr/bin:/bin", "npx"]))
    await asyncio.wait_for(m.connect_all(), timeout=15)
    assert m.status()[0]["status"].startswith("error")
    await m.disconnect("bad")
