import pytest
import httpx
from aon.engine import Agent
from aon.router import Plan, _SYS
from aon.llm_client import ChatResult
from aon.tools.base import ToolRegistry
from aon.approval_gate import AutoGate
from aon.memory import EpisodicMemory, RememberTool, contains_secret

FAKE_PAT = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


class LLM:
    def __init__(self): self.calls = 0
    async def chat(self, m, tools=None):
        self.calls += 1; return ChatResult("loop answer", [])


def _router(plan):
    async def r(goal, conversation=None): return plan
    return r


@pytest.mark.asyncio
async def test_direct_answer_claiming_an_action_is_not_trusted():
    llm = LLM()
    plan = Plan("", "answer", [], False, None,
                "The server has been added to AON with the proper headers. The connection request has been sent.")
    res = await Agent(llm, ToolRegistry(), AutoGate(), router=_router(plan)).run(FAKE_PAT)
    assert llm.calls == 1 and res.final == "loop answer"


@pytest.mark.asyncio
async def test_plain_direct_answer_still_short_circuits():
    llm = LLM()
    plan = Plan("", "answer", [], False, None, "Python lists are ordered and mutable.")
    res = await Agent(llm, ToolRegistry(), AutoGate(), router=_router(plan)).run("what is a list?")
    assert llm.calls == 0 and res.final.startswith("Python lists")


def test_router_prompt_forbids_claiming_actions():
    assert "never claim" in _SYS.lower()


@pytest.mark.parametrize("text", [
    f"token {FAKE_PAT}", "github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz0123",
    "sk-proj-abcdefghijklmnopqrstuvwxyz012345", "AKIAIOSFODNN7EXAMPLE",
    "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789",
    "-----BEGIN OPENSSH PRIVATE KEY-----", "xoxb-1234567890-abcdefghijkl"])
def test_contains_secret(text):
    assert contains_secret(text)


def test_contains_secret_negative():
    assert not contains_secret("User's favorite color is teal; uses GitHub MCP at api.githubcopilot.com")


@pytest.mark.asyncio
async def test_memory_never_stores_secrets(tmp_path):
    m = EpisodicMemory(tmp_path / "e.db")
    assert m.add(f"GitHub MCP added with PAT ({FAKE_PAT})") == -1
    assert m.all() == []
    r = await RememberTool(m).run({"content": f"my token is {FAKE_PAT}"})
    assert "secret" in r.output and m.all() == []


@pytest.mark.asyncio
async def test_fetch_text_raises_on_http_error():
    from aon.link_intake import fetch_text
    t = httpx.MockTransport(lambda req: httpx.Response(401, text="missing required Authorization header"))
    with pytest.raises(RuntimeError) as e:
        await fetch_text("https://api.githubcopilot.com/mcp/", transport=t)
    assert "401" in str(e.value)


@pytest.mark.asyncio
async def test_fetch_text_returns_readable_on_200():
    from aon.link_intake import fetch_text
    t = httpx.MockTransport(lambda req: httpx.Response(200, text="<html><body><h1>Hi</h1><script>x</script></body></html>"))
    assert (await fetch_text("https://x.dev", transport=t)).strip() == "Hi"
