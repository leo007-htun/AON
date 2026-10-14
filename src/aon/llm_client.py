import json
import httpx
from dataclasses import dataclass, field

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict

@dataclass
class ChatResult:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict | None = None
    finish_reason: str | None = None

def _parse_tool_calls(raw: list[dict]) -> list[ToolCall]:
    out = []
    for tc in raw or []:
        fn = tc.get("function", {})
        raw_args = fn.get("arguments", "") or ""
        try:
            args = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError:
            args = {"_parse_error": raw_args}
        out.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args))
    return out

class LLMClient:
    def __init__(self, base_url: str, model: str, http: httpx.AsyncClient | None = None,
                 sampling: dict | None = None, retry_without_thinking: bool = False):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.sampling = sampling or {}
        # vLLM: a reply cut off at the length limit while still thinking has no text; ask again
        # with thinking off (chat_template_kwargs is a vLLM field)
        self.retry_without_thinking = retry_without_thinking
        self._http = http
        self._owns_http = http is None
        self._can_tokenize = True

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=300)
        return self._http

    async def list_models(self) -> list[str]:
        r = await self._client().get(f"{self.base_url}/models")
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]

    async def count_tokens(self, messages: list[dict], tools: list[dict] | None = None) -> int | None:
        """The exact prompt size from the server's /tokenize (vLLM), or None where there is none
        (Ollama); a server without it is not asked again."""
        if not self._can_tokenize:
            return None
        root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        body: dict = {"model": self.model, "messages": messages, "add_generation_prompt": True}
        if tools:
            body["tools"] = tools
        try:
            r = await self._client().post(f"{root}/tokenize", json=body)
            if r.status_code in (404, 405):
                self._can_tokenize = False
                return None
            r.raise_for_status()
            return int(r.json()["count"])
        except Exception:
            return None

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        payload: dict = {"model": self.model, "messages": messages, "stream": False}
        payload.update(self.sampling)
        if tools:
            payload["tools"] = tools
        res = await self._post_chat(payload)
        if (self.retry_without_thinking and res.finish_reason == "length"
                and not res.content.strip() and not res.tool_calls):
            res = await self._post_chat(dict(payload, chat_template_kwargs={"enable_thinking": False}))
        return res

    async def _post_chat(self, payload: dict) -> ChatResult:
        r = await self._client().post(f"{self.base_url}/chat/completions", json=payload)
        r.raise_for_status()
        body = r.json()
        choice = body["choices"][0]
        msg = choice["message"]
        return ChatResult(
            content=msg.get("content") or "",
            tool_calls=_parse_tool_calls(msg.get("tool_calls")),
            usage=body.get("usage"),
            finish_reason=choice.get("finish_reason"),
        )
