"""MCP Hub: the Glama MCP directory (https://glama.ai) browsed inside AON.

Data comes from the Glama MCP API, which needs an API key. Its API Data License requires a
visible crawlable credit to Glama on every view showing the data, a link from each record to
its own Glama listing (the record's `url`), and boosted listings labelled as advertising; the
UI does all three. The key stays on this server and is never sent to the browser.
"""
from __future__ import annotations
import os, time
from pathlib import Path
import httpx

BASE = "https://glama.ai/api/mcp/v1"
UA = "AON-self-hosted-agent/1.0"


class HubError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class GlamaHub:
    def __init__(self, key_path: Path, transport=None, ttl: float = 600, env_key: str | None = None):
        self.key_path = Path(key_path)
        self._transport = transport
        self.ttl = ttl
        self._env_key = os.environ.get("GLAMA_API_KEY", "") if env_key is None else env_key
        self._cache: dict = {}

    def key(self) -> str:
        if self._env_key:
            return self._env_key
        try:
            return self.key_path.read_text().strip()
        except OSError:
            return ""

    def configured(self) -> bool:
        return bool(self.key())

    def set_key(self, key: str) -> None:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path.touch(mode=0o600, exist_ok=True)
        os.chmod(self.key_path, 0o600)
        self.key_path.write_text(key.strip() + "\n")
        self._cache.clear()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}, transport=self._transport)

    async def _get(self, path: str, params: dict | None = None, key: str | None = None) -> dict:
        key = key or self.key()
        if not key:
            raise HubError("no_key", "No Glama API key yet. Create one at https://glama.ai/settings/api-keys")
        params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        ck = (path, tuple(sorted(params.items())), key)
        hit = self._cache.get(ck)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        try:
            async with self._client() as c:
                r = await c.get(BASE + path, params=params, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as e:
            raise HubError("network", f"Glama is unreachable: {type(e).__name__}")
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code != 200:
            err = (data or {}).get("error") or {}
            raise HubError(err.get("code") or f"http_{r.status_code}", err.get("message") or f"Glama returned HTTP {r.status_code}")
        self._cache[ck] = (time.monotonic(), data)
        if len(self._cache) > 500:
            self._cache.pop(next(iter(self._cache)))
        return data

    async def check_key(self, key: str) -> bool:
        try:
            await self._get("/servers", {"first": 1}, key=key.strip())
            return True
        except HubError:
            return False

    async def servers(self, query: str = "", after: str | None = None, first: int = 24) -> dict:
        # newest-first browses the whole catalogue; a search ranks by relevance
        sort = "search-relevance:desc" if query else "added-at:desc"
        return await self._get("/servers", {"query": query, "after": after, "first": first, "sort": sort})

    async def server(self, namespace: str, slug: str) -> dict:
        return await self._get(f"/servers/{namespace}/{slug}")

    async def connectors(self, query: str = "", after: str | None = None, first: int = 24) -> dict:
        sort = "search-relevance:desc" if query else "recommended:desc"
        return await self._get("/connectors", {"query": query, "after": after, "first": first, "sort": sort})

    async def connector(self, namespace: str, slug: str) -> dict:
        return await self._get(f"/connectors/{namespace}/{slug}")
