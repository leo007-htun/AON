from agent.config import Config
from agent.build import build_registry
from agent.mcp_client import McpToolSpec


class FakeMgr:
    def tools(self): return [McpToolSpec("pw", "nav", "navigate", {})]


def _cfg(tmp): return Config("http://x/v1", "m", tmp, [], 5, 10, "127.0.0.1:8700")


def test_mcp_tools_registered(tmp_path):
    names = {t.name for t in build_registry(_cfg(tmp_path), mcp_manager=FakeMgr()).list()}
    assert "mcp__pw__nav" in names


def test_no_mcp_when_none(tmp_path):
    names = {t.name for t in build_registry(_cfg(tmp_path)).list()}
    assert not any(n.startswith("mcp__") for n in names)
