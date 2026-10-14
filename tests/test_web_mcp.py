from fastapi.testclient import TestClient
from aon.config import Config
from aon.persistence import Database
from aon.web import create_app
from aon.mcp_client import McpServerCfg


class FakeMgr:
    def __init__(self): self.servers = []
    def status(self): return [{"name": s.name, "status": "connected", "tools": [], "prompts": [],
                               "enabled": s.enabled, "command": s.command, "args": s.args} for s in self.servers]
    def list_servers(self): return list(self.servers)
    def add_server(self, cfg): self.servers.append(cfg)
    def remove_server(self, name): self.servers = [s for s in self.servers if s.name != name]
    def set_enabled(self, name, on):
        for s in self.servers:
            if s.name == name: s.enabled = on
    async def connect(self, cfg): pass
    async def connect_all(self): pass
    async def disconnect(self, name): pass


def _app(tmp):
    db = Database(str(tmp / "a.db"))
    def ma(g, o, m): raise AssertionError
    return create_app(Config("http://x/v1", "m", tmp, [], 5, 10, "127.0.0.1:8700"), db, ma,
                      mcp_manager=FakeMgr())


def test_add_list_toggle_delete(tmp_path):
    c = TestClient(_app(tmp_path))
    assert c.get("/api/mcp").json()["servers"] == []
    assert c.post("/api/mcp", json={"name": "pw", "command": "npx", "args": ["@playwright/mcp"]}).json()["ok"]
    assert c.get("/api/mcp").json()["servers"][0]["name"] == "pw"
    assert c.post("/api/mcp/pw/toggle", json={"enabled": False}).json()["ok"]
    assert c.request("DELETE", "/api/mcp/pw").json()["ok"]
    assert c.get("/api/mcp").json()["servers"] == []
