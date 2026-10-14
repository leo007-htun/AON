import asyncio, json
import httpx
from fastapi.testclient import TestClient
from agent.hub import GlamaHub, HubError
from agent.web import create_app
from agent.config import Config
from agent.persistence import Database

SERVER = {"id": "abc", "name": "Playwright MCP Server", "namespace": "microsoft", "slug": "playwright-mcp",
          "description": "Browser automation", "attributes": ["author:official"], "isBoosted": False,
          "qualityScore": 4.5, "repository": {"url": "https://github.com/microsoft/playwright-mcp"},
          "spdxLicense": {"name": "Apache License 2.0", "url": "https://spdx.org/licenses/Apache-2.0.json"},
          "thumbnailUrl": None, "environmentVariablesJsonSchema": None,
          "tools": [{"name": "browser_click", "description": "Click", "inputSchema": {}}],
          "url": "https://glama.ai/mcp/servers/microsoft/playwright-mcp"}


def _hub(tmp_path, handler, key="k-123"):
    seen = []
    def h(req):
        seen.append(req)
        return handler(req)
    hub = GlamaHub(tmp_path / ".glama_key", transport=httpx.MockTransport(h), env_key="")
    if key:
        hub.set_key(key)
    return hub, seen


def test_lists_servers_with_bearer_key_and_passes_search_and_cursor(tmp_path):
    hub, seen = _hub(tmp_path, lambda r: httpx.Response(200, json={
        "servers": [SERVER], "pageInfo": {"endCursor": "c2", "hasNextPage": True, "hasPreviousPage": False, "startCursor": "c1"}}))
    out = asyncio.run(hub.servers(query="browser", after="c1"))
    r = seen[0]
    assert r.headers["authorization"] == "Bearer k-123"
    assert r.url.path == "/api/mcp/v1/servers"
    assert r.url.params["query"] == "browser" and r.url.params["after"] == "c1"
    assert r.url.params["sort"] == "search-relevance:desc"      # a search ranks by relevance
    assert out["servers"][0]["url"] == SERVER["url"] and out["pageInfo"]["endCursor"] == "c2"


def test_no_search_lists_newest_first_and_caches_repeat_requests(tmp_path):
    hub, seen = _hub(tmp_path, lambda r: httpx.Response(200, json={"servers": [], "pageInfo": {}}))
    asyncio.run(hub.servers()); asyncio.run(hub.servers())
    assert len(seen) == 1 and seen[0].url.params["sort"] == "added-at:desc"


def test_server_detail_and_connectors(tmp_path):
    def h(r):
        if r.url.path.endswith("/servers/microsoft/playwright-mcp"):
            return httpx.Response(200, json=SERVER)
        return httpx.Response(200, json={"connectors": [], "facets": [], "pageInfo": {"endCursor": None, "hasNextPage": False}})
    hub, seen = _hub(tmp_path, h)
    assert asyncio.run(hub.server("microsoft", "playwright-mcp"))["tools"][0]["name"] == "browser_click"
    assert asyncio.run(hub.connectors(query="sentry"))["connectors"] == []
    assert seen[1].url.path == "/api/mcp/v1/connectors"


def test_missing_key_and_api_errors_are_reported(tmp_path):
    hub, seen = _hub(tmp_path, lambda r: httpx.Response(401, json={"error": {"code": "unauthorized", "message": "bad key"}}), key="")
    try:
        asyncio.run(hub.servers()); assert False
    except HubError as e:
        assert e.code == "no_key" and not seen
    hub.set_key("wrong")
    try:
        asyncio.run(hub.servers()); assert False
    except HubError as e:
        assert e.code == "unauthorized" and "bad key" in e.message


def test_key_is_saved_privately_and_survives_a_new_instance(tmp_path):
    hub, _ = _hub(tmp_path, lambda r: httpx.Response(200, json={}), key="secret-1")
    p = tmp_path / ".glama_key"
    assert p.read_text().strip() == "secret-1" and (p.stat().st_mode & 0o077) == 0
    assert GlamaHub(p, env_key="").configured()


class FakeHub:
    def __init__(self, key=True): self.key_ok = key; self.calls = []
    def configured(self): return self.key_ok
    async def check_key(self, k): self.calls.append(("check", k)); return k == "good"
    def set_key(self, k): self.calls.append(("set", k)); self.key_ok = True
    async def servers(self, query="", after=None): self.calls.append(("servers", query, after)); return {"servers": [SERVER], "pageInfo": {}}
    async def server(self, ns, slug): return SERVER
    async def connectors(self, query="", after=None): return {"connectors": [], "pageInfo": {}}
    async def connector(self, ns, slug): return {"name": "Sentry"}


def _client(tmp_path, hub):
    db = Database(str(tmp_path / "d.db"))
    def ma(g, o, m): raise AssertionError
    return TestClient(create_app(Config("http://x/v1", "gpt-oss", tmp_path, [], 5, 10, "127.0.0.1:8700"), db, ma, hub=hub))


def test_hub_endpoints_proxy_glama_and_never_return_the_key(tmp_path):
    hub = FakeHub()
    c = _client(tmp_path, hub)
    assert c.get("/api/hub/glama/status").json() == {"configured": True}
    d = c.get("/api/hub/mcp/servers", params={"query": "browser", "after": "c1"}).json()
    assert d["servers"][0]["url"] == SERVER["url"] and hub.calls[-1] == ("servers", "browser", "c1")
    assert c.get("/api/hub/mcp/servers/microsoft/playwright-mcp").json()["name"] == SERVER["name"]
    assert c.get("/api/hub/mcp/connectors").json()["connectors"] == []
    assert c.get("/api/hub/mcp/connectors/dev.sentry/sentry").json()["name"] == "Sentry"
    assert "good" not in json.dumps(c.get("/api/hub/glama/status").json())


def test_hub_key_is_checked_before_it_is_saved(tmp_path):
    hub = FakeHub(key=False)
    c = _client(tmp_path, hub)
    assert c.get("/api/hub/glama/status").json() == {"configured": False}
    assert "error" in c.post("/api/hub/glama/key", json={"key": "bad"}).json() and ("set", "bad") not in hub.calls
    assert c.post("/api/hub/glama/key", json={"key": "good"}).json() == {"ok": True} and ("set", "good") in hub.calls


def test_page_has_a_hub_with_skills_and_mcp_and_glama_credit(tmp_path):
    html = _client(tmp_path, FakeHub()).get("/").text
    assert 'data-modal="hub"' in html and 'id="modal-hub"' in html
    assert "SKILLS HUB" in html and "MCP HUB" in html
    # API Data License 15.3/15.4: per-view crawlable credit, per-record listing link, boosted labelled
    assert '<a href="https://glama.ai"' in html and "Glama</a>" in html
    assert "s.url" in html and "isBoosted" in html
