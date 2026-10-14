import httpx, pytest
from aon.tools.web import WebFetchTool, WebSearchTool

@pytest.mark.asyncio
async def test_fetch_strips_html():
    def h(req): return httpx.Response(200, text="<html><body><p>Hello <b>World</b></p></body></html>")
    async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as http:
        res = await WebFetchTool(http=http).run({"url": "http://x"})
    assert res.ok and "Hello World" in res.output and "<p>" not in res.output

@pytest.mark.asyncio
async def test_fetch_network_error_not_ok():
    def h(req): raise httpx.ConnectError("boom")
    async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as http:
        res = await WebFetchTool(http=http).run({"url": "http://x"})
    assert not res.ok

@pytest.mark.asyncio
async def test_search_parses_results():
    html = '<a class="result__a" href="http://a.com">Title A</a><a class="result__a" href="http://b.com">Title B</a>'
    def h(req): return httpx.Response(200, text=html)
    async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as http:
        res = await WebSearchTool(http=http).run({"query": "x"})
    assert res.ok and "Title A" in res.output and "http://b.com" in res.output
