"""AON as an MCP server: the tools other AI apps see, and what each one does."""
import re
import zlib
import pytest
from aon.mcp_server import AonService, build_server
from aon.persistence import Database
from aon.memory import MemoryStore
from aon.docs import DocStore
from aon.engine import RunResult
from aon.recall import _unit
from aon.config import Config


class FakeEmbedder:
    model = "fake"
    async def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 128
            for w in re.findall(r"[a-z]{3,}", re.sub(r"^search_(document|query): ", "", t).lower()):
                v[zlib.crc32(w.encode()) % 128] += 1.0
            out.append(_unit(v))
        return out


class FakeAgent:
    def __init__(self, gate, log): self.gate, self.log = gate, log
    async def run(self, goal, conversation=None, **kw):
        from aon.approval_gate import PendingAction
        decision = await self.gate.request(PendingAction("run_command", {"command": "rm -rf /tmp/x"}))
        self.log.append({"goal": goal, "history": conversation, "approved": decision.approved})
        return RunResult("done", f"answer to: {goal}", [])


def _service(tmp_path):
    cfg = Config("http://x/v1", "aon", tmp_path, [], 5, 10, "127.0.0.1:8700")
    log = []
    svc = AonService(cfg, db=Database(str(tmp_path / "app.db")), docs=DocStore(tmp_path / "docs.db"),
                     mem=MemoryStore(tmp_path / "episodes.db"), embedder=FakeEmbedder(),
                     make_agent=lambda gate, on_event, model, convo: FakeAgent(gate, log))
    return svc, log


@pytest.mark.asyncio
async def test_tools_listed_with_read_only_hints(tmp_path):
    svc, _ = _service(tmp_path)
    tools = {t.name: t for t in await build_server(svc).list_tools()}
    assert set(tools) == {"ask_aon", "search_memory", "search_documents", "list_documents",
                          "add_document", "remember"}
    for name in ("search_memory", "search_documents", "list_documents"):
        assert tools[name].annotations.readOnlyHint is True, name
    for name in ("ask_aon", "add_document", "remember"):
        assert tools[name].annotations.readOnlyHint is False, name
    assert all(t.description for t in tools.values())


@pytest.mark.asyncio
async def test_ask_refuses_machine_changes_and_keeps_a_chat(tmp_path):
    svc, log = _service(tmp_path)
    assert await svc.ask("hello") == "answer to: hello"
    assert log[0]["approved"] is False                  # shell commands and file writes are refused
    await svc.ask("and again")
    assert [t["content"] for t in log[1]["history"]] == ["hello", "answer to: hello"]
    await svc.ask("separate", chat="work")
    assert log[2]["history"] == []                       # a named chat has its own history


@pytest.mark.asyncio
async def test_ask_trims_the_reply_and_marks_unfinished_runs(tmp_path):
    svc, _ = _service(tmp_path)
    class Stuck:
        async def run(self, goal, conversation=None, **kw):
            return RunResult("stuck", "\n\nStopped: repeated the same call 3x\n", [])
    class Padded:
        async def run(self, goal, conversation=None, **kw):
            return RunResult("done", "\n\nPort 6543.\n", [])
    svc.make_agent = lambda gate, on_event, model, convo: Padded()
    assert await svc.ask("port?") == "Port 6543."
    svc.make_agent = lambda gate, on_event, model, convo: Stuck()
    assert await svc.ask("loop") == "[stuck] Stopped: repeated the same call 3x"


def test_request_logging_is_quiet(tmp_path):
    import logging
    from aon.mcp_server import quiet_logs
    quiet_logs()
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


@pytest.mark.asyncio
async def test_memory_tools(tmp_path):
    svc, _ = _service(tmp_path)
    assert "saved" in await svc.remember("The staging database is on port 6543.")
    assert "not saved" in await svc.remember("my token is ghp_" + "a" * 36)
    out = await svc.search_memory("staging database port")
    assert "6543" in out
    assert "no matching" in await svc.search_memory("zzzz qqqq")


@pytest.mark.asyncio
async def test_document_tools(tmp_path):
    svc, _ = _service(tmp_path)
    assert "empty" in await svc.list_documents()
    out = await svc.add_document("runbook.md", "Backups run nightly at 02:00 UTC and are kept for 14 days.")
    assert "Added runbook.md" in out
    assert "already" in await svc.add_document("runbook.md", "Backups run nightly at 02:00 UTC and are kept for 14 days.")
    assert "runbook.md" in await svc.list_documents()
    hits = await svc.search_documents("how long are backups kept")
    assert "runbook.md" in hits and "14 days" in hits
    assert "Could not add" in await svc.add_document("empty.txt", "   ")


@pytest.mark.asyncio
async def test_tools_run_through_the_mcp_layer(tmp_path):
    svc, log = _service(tmp_path)
    server = build_server(svc)
    res = await server.call_tool("ask_aon", {"goal": "ping"})
    text = res[0][0].text if isinstance(res, tuple) else res[0].text
    assert text == "answer to: ping" and log[0]["goal"] == "ping"
