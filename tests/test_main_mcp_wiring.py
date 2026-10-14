import pytest
from aon.config import Config
from aon.build import build_registry
from aon.mcp_client import McpManager, McpServerCfg


async def _fake_conn(cfg):
    class S:
        async def initialize(self): pass
        async def list_tools(self):
            t = type("T", (), {"name": "nav", "description": "d", "inputSchema": {}})()
            return type("R", (), {"tools": [t]})()
        async def list_prompts(self): return type("R", (), {"prompts": []})()
    return S(), (lambda: None)


@pytest.mark.asyncio
async def test_registry_includes_mcp_after_connect(tmp_path):
    m = McpManager(str(tmp_path / "mcp.json"), connector=_fake_conn)
    m.add_server(McpServerCfg("pw", "npx", ["x"]))
    await m.connect_all()
    cfg = Config("http://x/v1", "m", tmp_path, [], 5, 10, "127.0.0.1:8700")
    assert "mcp__pw__nav" in {t.name for t in build_registry(cfg, mcp_manager=m).list()}
