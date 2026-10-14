import httpx
import pytest
from aon.llm_client import LLMClient


@pytest.mark.asyncio
async def test_chat_captures_usage():
    def handler(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}],
                                         "usage": {"total_tokens": 42}})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    res = await LLMClient("http://x/v1", "m", http).chat([{"role": "user", "content": "hi"}])
    assert res.content == "hi" and res.usage == {"total_tokens": 42}
