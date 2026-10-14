import json
import pytest
import agent.mcp_client as mc
from agent.mcp_client import McpManager, McpServerCfg


def test_cfg_roundtrip(tmp_path):
    p = str(tmp_path / "mcp.json")
    m = McpManager(p)
    m.add_server(McpServerCfg("gh", "", [], True, transport="http",
                              url="https://x/mcp", headers={"Authorization": "Bearer t"}))
    row = json.load(open(p))[0]
    assert row["transport"] == "http" and row["url"] == "https://x/mcp" and row["headers"]["Authorization"] == "Bearer t"
    m2 = McpManager(p)
    s = m2.list_servers()[0]
    assert s.transport == "http" and s.url == "https://x/mcp"


def test_old_rows_still_load(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps([{"name": "a", "command": "npx", "args": ["x"], "enabled": True}]))
    m = McpManager(str(p))
    s = m.list_servers()[0]
    assert s.name == "a" and s.transport == "stdio" and s.url == ""


@pytest.mark.asyncio
async def test_default_connector_dispatch(tmp_path, monkeypatch):
    async def fake_http(cfg): return ("HTTP", (lambda: None))
    async def fake_stdio(cfg): return ("STDIO", (lambda: None))
    monkeypatch.setattr(mc, "_http_connector", fake_http)
    monkeypatch.setattr(mc, "_stdio_connector", fake_stdio)
    m = McpManager(str(tmp_path / "mcp.json"))
    http_sess, _ = await m._default_connector(McpServerCfg("h", "", [], True, transport="http", url="u"))
    stdio_sess, _ = await m._default_connector(McpServerCfg("s", "npx", ["x"]))
    assert http_sess == "HTTP" and stdio_sess == "STDIO"
