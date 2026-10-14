import pytest
from agent.mcp_tools import McpTool
from agent.mcp_client import McpToolSpec


class FakeMgr:
    def __init__(self, fail=False): self.fail = fail; self.called = None
    async def call_tool(self, server, name, args):
        if self.fail: raise RuntimeError("boom")
        self.called = (server, name, args); return f"ok {name}"


@pytest.mark.asyncio
async def test_mcp_tool_calls_manager():
    spec = McpToolSpec("pw", "nav", "navigate", {"type": "object", "properties": {"url": {"type": "string"}}})
    mgr = FakeMgr()
    t = McpTool(mgr, spec)
    assert t.name == "mcp__pw__nav" and t.mutating is True and t.parameters["properties"]["url"]
    r = await t.run({"url": "http://x"})
    assert r.ok and "ok nav" in r.output and mgr.called == ("pw", "nav", {"url": "http://x"})


@pytest.mark.asyncio
async def test_mcp_tool_error():
    t = McpTool(FakeMgr(fail=True), McpToolSpec("pw", "nav", "d", {}))
    r = await t.run({})
    assert not r.ok and "boom" in r.error
