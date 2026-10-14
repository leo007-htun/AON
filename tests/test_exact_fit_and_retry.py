"""On vLLM: requests are fitted with the server's exact token count (dense text such as logs runs
well under 3 characters per token, so the estimate alone overshot the window and got HTTP 400),
and a reply cut off at the length limit with no text is retried once with thinking off."""
import json
import httpx
import pytest
from aon.llm_client import LLMClient, ChatResult
from aon.context import fit_exact, fit_messages, message_tokens, estimate_tokens
from aon.backends import make_chat_llm
from aon.config import Config
from aon.engine import Agent
from aon.tools.base import ToolRegistry
from aon.approval_gate import AutoGate


def _resp(message, finish="stop"):
    return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": finish}]})


# --- exact token count -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_count_tokens_asks_the_servers_tokenize_endpoint():
    seen = []
    def h(req):
        seen.append((req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"count": 1234, "max_model_len": 16384, "tokens": []})
    async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as http:
        c = LLMClient("http://vllm:8001/v1", "aon", http=http)
        n = await c.count_tokens([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    assert n == 1234
    path, body = seen[0]
    assert path == "/tokenize"                       # served at the root, not under /v1
    assert body["messages"][0]["content"] == "hi" and body["tools"] == [{"type": "function"}]
    assert body["add_generation_prompt"] is True


@pytest.mark.asyncio
async def test_count_tokens_is_none_where_unsupported_and_not_asked_again():
    calls = []
    def h(req):
        calls.append(req.url.path)
        return httpx.Response(404, json={"error": "not found"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as http:
        c = LLMClient("http://ol/v1", "gpt-oss", http=http)
        assert await c.count_tokens([{"role": "user", "content": "hi"}]) is None
        assert await c.count_tokens([{"role": "user", "content": "hi"}]) is None
    assert calls == ["/tokenize"]


def _big_msgs(n_hist=12):
    hist = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"h{i} " + "9.9.9.9 " * 400}
            for i in range(n_hist)]
    return [{"role": "system", "content": "sys " * 50}, *hist, {"role": "user", "content": "the request"}], n_hist


def _est(msgs, tools=None):
    return sum(message_tokens(m) for m in msgs) + (estimate_tokens(tools) if tools else 0)


@pytest.mark.asyncio
async def test_fit_exact_refits_when_the_real_count_is_over_the_limit():
    msgs, n_hist = _big_msgs()
    async def counter(m, tools):            # dense text: the real count is 1.6x the estimate
        return int(_est(m, tools) * 1.6)
    out = await fit_exact(msgs, n_hist, None, 3000, counter)
    assert await counter(out, None) <= 3000
    assert out[-1]["content"] == "the request" and out[0]["role"] == "system"


@pytest.mark.asyncio
async def test_fit_exact_keeps_requests_that_already_fit():
    msgs, n_hist = _big_msgs(2)
    async def counter(m, tools): return 100
    out = await fit_exact(msgs, n_hist, None, 3000, counter)
    assert out == fit_messages(msgs, n_hist, None, 3000)[0]


@pytest.mark.asyncio
async def test_fit_exact_falls_back_to_the_estimate_without_a_counter():
    msgs, n_hist = _big_msgs()
    async def counter(m, tools): return None
    out = await fit_exact(msgs, n_hist, None, 3000, counter)
    assert out == fit_messages(msgs, n_hist, None, 3000)[0]


# --- empty reply cut off by the length limit ---------------------------------------------------

@pytest.mark.asyncio
async def test_cut_off_empty_reply_is_retried_with_thinking_off():
    bodies = []
    def h(req):
        bodies.append(json.loads(req.content))
        if len(bodies) == 1:
            return _resp({"role": "assistant", "content": None, "reasoning": "long thoughts"}, finish="length")
        return _resp({"role": "assistant", "content": "the answer"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as http:
        c = LLMClient("http://vllm:8001/v1", "aon", http=http, retry_without_thinking=True)
        res = await c.chat([{"role": "user", "content": "explain"}])
    assert res.content == "the answer" and len(bodies) == 2
    assert "chat_template_kwargs" not in bodies[0]
    assert bodies[1]["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("message,finish", [
    ({"role": "assistant", "content": "partial text"}, "length"),     # some text came back
    ({"role": "assistant", "content": ""}, "stop"),                   # empty, but not cut off
    ({"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "function": {"name": "x", "arguments": "{}"}}]}, "length"),
])
async def test_no_retry_unless_cut_off_with_nothing(message, finish):
    n = []
    def h(req):
        n.append(1); return _resp(message, finish=finish)
    async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as http:
        c = LLMClient("http://vllm:8001/v1", "aon", http=http, retry_without_thinking=True)
        res = await c.chat([{"role": "user", "content": "x"}])
    assert len(n) == 1 and res.finish_reason == finish


@pytest.mark.asyncio
async def test_no_retry_when_not_enabled():
    n = []
    def h(req):
        n.append(1); return _resp({"role": "assistant", "content": None}, finish="length")
    async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as http:
        c = LLMClient("http://ol/v1", "gpt-oss", http=http)
        res = await c.chat([{"role": "user", "content": "x"}])
    assert len(n) == 1 and res.content == "" and res.finish_reason == "length"


def test_only_the_vllm_chat_model_retries(tmp_path):
    cfg = Config(ollama_base_url="http://ol/v1", default_model="aon", workdir=tmp_path, always_ask=[],
                 max_steps=5, command_timeout=10, bind="127.0.0.1:8700",
                 chat_base_url="http://vllm:8001/v1", chat_model="aon")
    assert make_chat_llm(cfg, "aon").retry_without_thinking is True
    assert make_chat_llm(cfg, "gpt-oss").retry_without_thinking is False


# --- the engine ---------------------------------------------------------------------------------

class CountingLLM:
    """Reports 1.6x the estimate as the real count; records what was sent."""
    def __init__(self, reply): self.reply = reply; self.sent = []
    async def count_tokens(self, messages, tools=None):
        return int(_est(messages, tools) * 1.6)
    async def chat(self, messages, tools=None):
        self.sent.append(messages); return self.reply


@pytest.mark.asyncio
async def test_engine_sends_requests_that_fit_by_the_exact_count():
    llm = CountingLLM(ChatResult(content="ok"))
    conv = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"t{i} " + "1.2.3.4 " * 400}
            for i in range(12)]
    res = await Agent(llm, ToolRegistry(), AutoGate(), context_limit=3000).run("go", conversation=conv)
    assert res.status == "done"
    assert await llm.count_tokens(llm.sent[0], ToolRegistry().schemas()) <= 3000


@pytest.mark.asyncio
async def test_engine_says_when_the_reply_was_cut_off():
    class L:
        async def chat(self, messages, tools=None):
            return ChatResult(content="", finish_reason="length")
    res = await Agent(L(), ToolRegistry(), AutoGate()).run("explain everything")
    assert res.status == "stuck" and "cut off" in res.final
