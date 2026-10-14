import re, html as _html
import httpx
from agent.tools.base import Tool, ToolResult

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\n\s*\n+")

def _readable(text: str, limit: int = 20000) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = _TAG.sub(" ", text)
    text = _html.unescape(text)
    text = _WS.sub("\n\n", re.sub(r"[ \t]+", " ", text)).strip()
    return text[:limit]

class WebFetchTool(Tool):
    name = "web_fetch"; description = "Fetch a URL and return readable text."
    parameters = {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}
    mutating = False
    def __init__(self, http: httpx.AsyncClient | None = None): self._http = http
    async def run(self, args: dict) -> ToolResult:
        http = self._http or httpx.AsyncClient(timeout=30, follow_redirects=True)
        try:
            r = await http.get(args["url"], headers={"User-Agent": "Mozilla/5.0 agent"})
            return ToolResult(ok=True, output=_readable(r.text))
        except httpx.HTTPError as e:
            return ToolResult(ok=False, output="", error=f"fetch failed: {e}")

class WebSearchTool(Tool):
    name = "web_search"; description = "Web search; returns top result titles and URLs."
    parameters = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    mutating = False
    def __init__(self, http: httpx.AsyncClient | None = None): self._http = http
    async def run(self, args: dict) -> ToolResult:
        http = self._http or httpx.AsyncClient(timeout=30, follow_redirects=True)
        try:
            r = await http.get("https://html.duckduckgo.com/html/",
                               params={"q": args["query"]},
                               headers={"User-Agent": "Mozilla/5.0 agent"})
            hits = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S)
            if not hits:
                return ToolResult(ok=True, output="(no results)")
            lines = [f"- {_TAG.sub('', t).strip()} :: {u}" for u, t in hits[:8]]
            return ToolResult(ok=True, output="\n".join(lines))
        except httpx.HTTPError as e:
            return ToolResult(ok=False, output="", error=f"search failed: {e}")
