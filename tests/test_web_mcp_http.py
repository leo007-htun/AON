from fastapi.testclient import TestClient
from aon.config import Config
from aon.persistence import Database
from aon.web import create_app


class FakeMgr:
    def __init__(self): self.last = None; self.servers = []
    def status(self): return [{"name": s.name, "status": "connected", "tools": [], "prompts": [],
                               "enabled": s.enabled, "command": s.command, "args": s.args,
                               "transport": s.transport, "url": s.url} for s in self.servers]
    def list_servers(self): return list(self.servers)
    def add_server(self, cfg): self.last = cfg; self.servers.append(cfg)
    def remove_server(self, n): pass
    def set_enabled(self, n, o): pass
    async def connect(self, c): pass
    async def connect_all(self): pass
    async def disconnect(self, n): pass


def test_add_http_server(tmp_path):
    mgr = FakeMgr()
    db = Database(str(tmp_path / "a.db"))
    def ma(g, o, m): raise AssertionError
    app = create_app(Config("http://x/v1", "m", tmp_path, [], 5, 10, "127.0.0.1:8700"), db, ma, mcp_manager=mgr)
    c = TestClient(app)
    r = c.post("/api/mcp", json={"name": "gh", "transport": "http", "url": "https://x/mcp",
                                 "headers": {"Authorization": "Bearer t"}})
    assert r.json()["ok"]
    assert mgr.last.transport == "http" and mgr.last.url == "https://x/mcp"
    assert mgr.last.headers["Authorization"] == "Bearer t"
