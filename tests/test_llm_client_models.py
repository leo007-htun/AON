import httpx, pytest
from agent.llm_client import LLMClient

@pytest.mark.asyncio
async def test_list_models_parses_ids():
    def handler(req):
        assert req.url.path.endswith("/models")
        return httpx.Response(200, json={"data": [{"id": "gpt-oss"}, {"id": "llama3"}]})
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        c = LLMClient("http://x/v1", "gpt-oss", http=http)
        assert await c.list_models() == ["gpt-oss", "llama3"]
