import httpx, pytest
from aon.llm_client import LLMClient, ChatResult, ToolCall

def _resp(message):
    return httpx.Response(200, json={"choices": [{"message": message}]})

@pytest.mark.asyncio
async def test_chat_returns_content():
    def h(req): return _resp({"role": "assistant", "content": "hello"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as http:
        c = LLMClient("http://x/v1", "gpt-oss", http=http)
        res = await c.chat([{"role": "user", "content": "hi"}])
        assert isinstance(res, ChatResult)
        assert res.content == "hello"
        assert res.tool_calls == []

@pytest.mark.asyncio
async def test_chat_parses_tool_calls():
    def h(req):
        return _resp({"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "function": {"name": "run_command", "arguments": '{"command": "ls"}'}}
        ]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as http:
        c = LLMClient("http://x/v1", "gpt-oss", http=http)
        res = await c.chat([{"role": "user", "content": "list"}], tools=[{"type": "function"}])
        assert res.tool_calls == [ToolCall(id="c1", name="run_command", arguments={"command": "ls"})]

@pytest.mark.asyncio
async def test_chat_malformed_tool_args_does_not_raise():
    def h(req):
        return _resp({"role": "assistant", "tool_calls": [
            {"id": "c2", "function": {"name": "x", "arguments": "{not json"}}
        ]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as http:
        c = LLMClient("http://x/v1", "gpt-oss", http=http)
        res = await c.chat([{"role": "user", "content": "x"}])
        assert res.tool_calls[0].arguments["_parse_error"] == "{not json"
