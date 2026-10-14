import json
import pytest
import httpx
from agent.link_intake import LinkIntake, IntakeResult, find_urls, OPTIONS
from agent.persistence import Database
from agent.llm_client import ChatResult, ToolCall
from agent.engine import Agent
from agent.tools.base import Tool, ToolResult, ToolRegistry
from agent.approval_gate import AutoGate

MCP_PAGE = ("Playwright MCP. Add to your MCP client: {\"mcpServers\": {\"playwright\": "
            "{\"command\": \"npx\", \"args\": [\"@playwright/mcp@latest\"]}}}")
ARTICLE = "A blog post about why sourdough needs a long fermentation."


class LLM:
    """Returns the given classification JSON for every chat call."""
    def __init__(self, reply): self.reply = reply; self.calls = 0
    async def chat(self, m, tools=None):
        self.calls += 1
        return ChatResult(self.reply if isinstance(self.reply, str) else json.dumps(self.reply), [])


def _intake(tmp_path, page, cls, convo="c1", fetched=None):
    async def fetch(url):
        if fetched is not None:
            fetched.append(url)
        if isinstance(page, Exception):
            raise page
        return page
    return LinkIntake(fetch, LLM(cls), Database(str(tmp_path / "d.db")), convo)


def test_find_urls():
    assert find_urls("see https://a.dev/x, and (http://b.io/y).") == ["https://a.dev/x", "http://b.io/y"]
    assert find_urls("no links here") == []


@pytest.mark.asyncio
async def test_bare_link_asks_with_content_based_options(tmp_path):
    it = _intake(tmp_path, MCP_PAGE, {"kind": "mcp_server", "title": "Playwright MCP",
                                      "summary": "Browser automation server.", "user_intent": None})
    r = await it("https://playwright.dev/docs/getting-started-mcp")
    assert r.ask and "Playwright MCP" in r.ask and "Browser automation server." in r.ask
    assert "1. " + OPTIONS["install_mcp"] in r.ask
    assert "2. " + OPTIONS["summarize"] in r.ask


@pytest.mark.asyncio
async def test_reply_number_routes_to_install(tmp_path):
    cls = {"kind": "mcp_server", "title": "Playwright MCP", "summary": "s", "user_intent": None}
    fetched = []
    it = _intake(tmp_path, MCP_PAGE, cls, fetched=fetched)
    await it("https://playwright.dev/docs/getting-started-mcp")
    r = await it("1")
    assert r.intent == "install_mcp" and "add_mcp_server" in r.goal
    assert "https://playwright.dev/docs/getting-started-mcp" in r.goal and "@playwright/mcp" in r.goal
    assert await it("1") is None            # pending choice is consumed


@pytest.mark.asyncio
async def test_reply_keyword_routes_to_summarize(tmp_path):
    it = _intake(tmp_path, ARTICLE, {"kind": "article", "title": "Sourdough", "summary": "s", "user_intent": None})
    r = await it("https://blog.example.com/sourdough")
    assert OPTIONS["install_mcp"] not in r.ask           # options follow the content
    r = await it("just summarize it please")
    assert r.intent == "summarize" and "sourdough" in r.goal.lower()


@pytest.mark.asyncio
async def test_reply_no_cancels(tmp_path):
    it = _intake(tmp_path, ARTICLE, {"kind": "article", "title": "T", "summary": "s", "user_intent": None})
    await it("https://blog.example.com/x")
    r = await it("nothing, thanks")
    assert r.final and r.goal is None


@pytest.mark.asyncio
async def test_unrelated_reply_falls_through_and_clears(tmp_path):
    it = _intake(tmp_path, ARTICLE, {"kind": "article", "title": "T", "summary": "s", "user_intent": None})
    await it("https://blog.example.com/x")
    assert await it("what's the weather like?") is None
    assert await it("2") is None


@pytest.mark.asyncio
async def test_stated_intent_skips_question(tmp_path):
    it = _intake(tmp_path, MCP_PAGE, {"kind": "mcp_server", "title": "P", "summary": "s",
                                      "user_intent": "install_mcp"})
    r = await it("install this mcp https://playwright.dev/docs/getting-started-mcp")
    assert r.ask is None and r.intent == "install_mcp"


@pytest.mark.asyncio
async def test_heuristic_kind_when_llm_fails(tmp_path):
    it = _intake(tmp_path, MCP_PAGE, "not json at all")
    r = await it("https://x.dev/mcp")
    assert OPTIONS["install_mcp"] in r.ask


@pytest.mark.asyncio
async def test_skill_repo_offers_import(tmp_path):
    page = "obra/superpowers - skills/brainstorming/SKILL.md - a skills library"
    it = _intake(tmp_path, page, {"kind": "skill_repo", "title": "Superpowers", "summary": "s", "user_intent": None})
    r = await it("https://github.com/obra/superpowers")
    assert "1. " + OPTIONS["import_skills"] in r.ask
    r = await it("import them")
    assert r.intent == "import_skills" and "import_skills" in r.goal


@pytest.mark.asyncio
async def test_fetch_failure_falls_through(tmp_path):
    it = _intake(tmp_path, RuntimeError("403"), {"kind": "article"})
    assert await it("https://blocked.example.com/") is None


@pytest.mark.asyncio
async def test_no_link_no_pending_is_none_and_no_llm_call(tmp_path):
    it = _intake(tmp_path, ARTICLE, {"kind": "article"})
    assert await it("hello there") is None
    assert it.llm.calls == 0


def test_pending_link_store(tmp_path):
    db = Database(str(tmp_path / "d.db"))
    assert db.get_pending_link("c") is None
    db.set_pending_link("c", {"url": "u"})
    assert db.get_pending_link("c") == {"url": "u"} and db.get_pending_link("other") is None
    db.clear_pending_link("c")
    assert db.get_pending_link("c") is None


# ---- engine integration ----

class EchoLLM:
    def __init__(self): self.user = None; self.calls = 0
    async def chat(self, m, tools=None):
        self.calls += 1; self.user = m[-1]["content"]; return ChatResult("answer", [])


@pytest.mark.asyncio
async def test_agent_returns_intake_question_without_llm():
    llm = EchoLLM()
    async def intake(msg, emit=None): return IntakeResult(ask="What would you like to do?")
    res = await Agent(llm, ToolRegistry(), AutoGate(), intake=intake).run("https://x.dev")
    assert res.status == "clarify" and res.final == "What would you like to do?" and llm.calls == 0


@pytest.mark.asyncio
async def test_agent_runs_resolved_goal_and_skips_router():
    llm = EchoLLM(); routed = []
    async def intake(msg, emit=None): return IntakeResult(goal="Summarize page X", intent="summarize")
    async def router(goal, conversation=None): routed.append(goal)
    res = await Agent(llm, ToolRegistry(), AutoGate(), intake=intake, router=router).run("2")
    assert res.final == "answer" and llm.user == "Summarize page X" and routed == []


# ---- tools ----

class FakeMgr:
    def __init__(self, status="connected"): self.added = []; self._status = status
    def add_server(self, cfg): self.added.append(cfg)
    async def connect(self, cfg): pass
    def status(self):
        return [{"name": c.name, "status": self._status, "tools": ["a", "b"]} for c in self.added]


@pytest.mark.asyncio
async def test_add_mcp_server_tool_is_gated_and_reports():
    from agent.install_tools import AddMcpServerTool
    m = FakeMgr(); t = AddMcpServerTool(m)
    assert t.mutating is True
    r = await t.run({"name": "pw", "command": "/x/npx", "args": ["-y", "@playwright/mcp@latest"]})
    assert r.ok and "connected" in r.output and "a, b" in r.output
    assert m.added[0].command == "/x/npx" and m.added[0].transport == "stdio"
    r = await t.run({"name": "remote", "transport": "http", "url": "https://h/mcp",
                     "headers": {"Authorization": "Bearer k"}})
    assert m.added[1].url == "https://h/mcp" and m.added[1].headers == {"Authorization": "Bearer k"}


@pytest.mark.asyncio
async def test_add_mcp_server_tool_errors():
    from agent.install_tools import AddMcpServerTool
    t = AddMcpServerTool(FakeMgr(status="error: HTTP 401"))
    r = await t.run({"name": "x", "transport": "http", "url": "https://h/mcp"})
    assert not r.ok and "401" in r.error
    assert not (await t.run({"name": "y"})).ok                  # stdio needs a command
    assert not (await t.run({"name": "../bad", "command": "c"})).ok


@pytest.mark.asyncio
async def test_import_skills_tool(tmp_path, monkeypatch):
    import io, zipfile
    import agent.skill_import as si
    from agent.install_tools import ImportSkillsTool
    from agent.skills import SkillStore
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("r-1/skills/a/SKILL.md", "---\nname: a\ndescription: does a\n---\nbody")
        z.writestr("r-1/skills/b/SKILL.md", "---\nname: b\ndescription: does b\n---\nbody")
    async def fake(url): return buf.getvalue()
    monkeypatch.setattr(si, "fetch_github_zip", fake)
    store = SkillStore(tmp_path / "sk"); t = ImportSkillsTool(store)
    assert t.mutating is True
    r = await t.run({"url": "https://github.com/o/r", "names": ["b"]})
    assert r.ok and "b" in r.output and [n for n, _ in store.list()] == ["b"]


# ---- HTTP MCP preflight ----

@pytest.mark.asyncio
async def test_http_preflight_raises_on_401():
    from agent.mcp_client import _http_preflight, McpServerCfg
    def handler(req): return httpx.Response(401, json={"error": "auth.failed"})
    cfg = McpServerCfg("t", transport="http", url="https://h/mcp")
    with pytest.raises(RuntimeError) as e:
        await _http_preflight(cfg, transport=httpx.MockTransport(handler))
    assert "401" in str(e.value) and "auth.failed" in str(e.value)


@pytest.mark.asyncio
async def test_http_preflight_ok_on_200():
    from agent.mcp_client import _http_preflight, McpServerCfg
    def handler(req):
        assert req.headers["authorization"] == "Bearer k"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
    cfg = McpServerCfg("t", transport="http", url="https://h/mcp", headers={"Authorization": "Bearer k"})
    await _http_preflight(cfg, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_add_mcp_server_adds_npx_yes_flag():
    from agent.install_tools import AddMcpServerTool
    m = FakeMgr(); t = AddMcpServerTool(m)
    await t.run({"name": "pw", "command": "/home/ubuntu/app/bin/npx", "args": ["@playwright/mcp@latest"]})
    assert m.added[0].args == ["-y", "@playwright/mcp@latest"]
    await t.run({"name": "pw2", "command": "npx", "args": ["--yes", "pkg"]})
    assert m.added[1].args == ["--yes", "pkg"]


def test_playbook_mentions_chromium_and_yes():
    from agent.link_intake import mcp_playbook
    p = mcp_playbook()
    assert "--browser=chromium" in p and "-y" in p


@pytest.mark.asyncio
async def test_docs_about_mcp_still_offer_install(tmp_path):
    it = _intake(tmp_path, MCP_PAGE, {"kind": "docs", "title": "Playwright MCP docs", "summary": "s",
                                      "user_intent": None})
    r = await it("https://playwright.dev/docs/getting-started-mcp")
    assert "1. " + OPTIONS["install_mcp"] in r.ask
    r = await it("install it")
    assert r.intent == "install_mcp"
