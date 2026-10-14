# GUARDRAIL MODULE -- not editable by agent tools (see config.GUARDRAIL_PATHS).
from __future__ import annotations
import re
import httpx

_DEC = re.compile(r"^(ok|no)\s+(\d+)$", re.I)

def parse_decision(text: str):
    m = _DEC.match(text.strip())
    if not m:
        return None
    return int(m.group(2)), m.group(1).lower() == "ok"

class Telegram:
    def __init__(self, token: str, allowed_ids: list[int], http: httpx.AsyncClient | None = None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.allowed = set(int(x) for x in allowed_ids)
        self._http = http
    def _client(self):
        return self._http or httpx.AsyncClient(timeout=70)
    async def notify(self, text: str) -> None:
        for cid in self.allowed:
            await self._client().get(f"{self.base}/sendMessage",
                                      params={"chat_id": cid, "text": text})
    async def poll_decisions(self, offset: int):
        r = await self._client().get(f"{self.base}/getUpdates",
                                      params={"offset": offset, "timeout": 50})
        texts = []; new_off = offset
        for u in r.json().get("result", []):
            new_off = u["update_id"] + 1
            m = u.get("message") or {}
            if (m.get("from") or {}).get("id") in self.allowed and m.get("text"):
                texts.append(m["text"])
        return new_off, texts
