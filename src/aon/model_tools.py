"""Ollama model management: pull GGUF models (Hugging Face or Ollama library), list, remove.

Ollama here is the GPU box behind the local relay (cfg.ollama_base_url). Hugging Face GGUF
repos are pulled as `hf.co/<user>/<repo>:<quant>`; when no quant is given, the best one
that fits the GPU budget is chosen from the repo's file list.
"""
from __future__ import annotations
import json
import re
import httpx
from aon.tools.base import Tool, ToolResult

GB = 1024 ** 3
# preferred quantizations, best quality/size trade-off first
QUANT_ORDER = ["Q4_K_M", "Q5_K_M", "Q4_K_S", "Q4_0", "IQ4_XS", "Q5_K_S", "Q6_K", "Q3_K_L",
               "Q3_K_M", "IQ3_M", "IQ3_XS", "Q3_K_S", "Q2_K", "IQ2_M", "IQ2_XS", "Q8_0", "F16", "BF16"]
_QUANT_RE = re.compile(r"(?:^|[-_.])(IQ\d_[A-Z]+|Q\d_K_[SML]|Q\d_K|Q\d_\d(?:_\d_\d)?|BF16|F16|F32)(?=[-_.])", re.I)
_SHARD_RE = re.compile(r"-\d{5}-of-\d{5}\.gguf$", re.I)
_HF_RE = re.compile(r"^(?:https?://)?(?:www\.)?(?:huggingface\.co|hf\.co)/([\w.-]+/[\w.-]+?)"
                    r"(?::([\w.-]+))?(?:/(?:blob|resolve|tree)/[^/]+(?:/(.*))?)?/?$", re.I)


def ollama_root(base_url: str) -> str:
    return re.sub(r"/v1/?$", "", (base_url or "").rstrip("/"))


def parse_model_ref(ref: str):
    """('hf', 'user/repo', quant|None) for Hugging Face refs, else ('ollama', name, None)."""
    ref = (ref or "").strip()
    m = _HF_RE.match(ref)
    if m:
        repo, quant, path = m.groups()
        if not quant and path:
            q = _QUANT_RE.search("/" + path.split("/")[-1])
            quant = q.group(1).upper() if q else None
        return ("hf", repo, quant.upper() if quant else None)
    return ("ollama", ref, None)


def pick_quant(sizes: dict, max_bytes: float):
    for q in QUANT_ORDER:
        if q in sizes and sizes[q] <= max_bytes:
            return q
    return None


def _fmt(n) -> str:
    return f"{n / GB:.1f} GB"


class _OllamaTool(Tool):
    def __init__(self, base_url: str, transport=None):
        self.root = ollama_root(base_url)
        self._transport = transport

    def _client(self, read_timeout=60):
        return httpx.AsyncClient(timeout=httpx.Timeout(30, read=read_timeout),
                                 transport=self._transport, follow_redirects=True)


class PullModelTool(_OllamaTool):
    name = "pull_model"
    description = (
        "Download a model into Ollama (the GPU box) so it appears in AON's model list. model= a "
        "Hugging Face GGUF repo URL, hf.co/<user>/<repo>[:<quant>], or an Ollama library name "
        "(e.g. qwen3:8b). For Hugging Face repos the best quantization that fits the GPU is chosen. "
        "Reports the size and whether the model supports tool calling.")
    parameters = {"type": "object", "properties": {
        "model": {"type": "string"},
        "force": {"type": "boolean", "description": "pull even if larger than the GPU budget"}},
        "required": ["model"]}
    mutating = True

    def __init__(self, base_url: str, max_gb: float = 22, transport=None):
        super().__init__(base_url, transport)
        self.max_gb = max_gb

    async def _hf_sizes(self, client, repo):
        r = await client.get(f"https://huggingface.co/api/models/{repo}/tree/main",
                             params={"recursive": "true"})
        if r.status_code != 200:
            raise RuntimeError(f"Hugging Face repo {repo} not found (HTTP {r.status_code})")
        sizes = {}
        for f in r.json():
            path = f.get("path", "")
            if f.get("type") != "file" or not path.lower().endswith(".gguf"):
                continue
            if "mmproj" in path.lower():
                continue                      # vision projector, not the model
            q = _QUANT_RE.search("/" + path.split("/")[-1])
            if q:
                size = (f.get("lfs") or {}).get("size") or f.get("size") or 0
                key = q.group(1).upper()
                if _SHARD_RE.search(path):
                    sizes[key] = sizes.get(key, 0) + size      # split model: parts add up
                else:
                    sizes.setdefault(key, size)
        return sizes

    async def run(self, args):
        kind, name, quant = parse_model_ref(args.get("model", ""))
        if not name:
            return ToolResult(ok=False, output="", error="model is required")
        budget = self.max_gb * GB
        size = None
        try:
            async with self._client(read_timeout=None) as c:
                if kind == "hf":
                    sizes = await self._hf_sizes(c, name)
                    if not sizes:
                        return ToolResult(ok=False, output="", error=f"{name} has no GGUF files")
                    if not quant:
                        quant = pick_quant(sizes, budget)
                        if not quant and not args.get("force"):
                            return ToolResult(ok=False, output="", error=(
                                f"no quantization of {name} fits the {self.max_gb} GB GPU budget "
                                f"(smallest is {_fmt(min(sizes.values()))}); pass force=true to pull anyway"))
                        quant = quant or min(sizes, key=sizes.get)
                    size = sizes.get(quant)
                    if size and size > budget and not args.get("force"):
                        return ToolResult(ok=False, output="", error=(
                            f"hf.co/{name}:{quant} is {_fmt(size)}, over the {self.max_gb} GB GPU "
                            "budget (it would spill to system RAM and run slowly); pass force=true "
                            "to pull anyway"))
                    ref = f"hf.co/{name}:{quant}"
                else:
                    ref = name
                last = ""
                async with c.stream("POST", f"{self.root}/api/pull", json={"model": ref, "stream": True}) as r:
                    if r.status_code >= 400:
                        return ToolResult(ok=False, output="", error=f"Ollama pull failed: HTTP {r.status_code}")
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        ev = json.loads(line)
                        if ev.get("error"):
                            return ToolResult(ok=False, output="", error=f"Ollama: {ev['error']}")
                        last = ev.get("status", last)
                if last != "success":
                    return ToolResult(ok=False, output="", error=f"pull did not finish (last status: {last or 'none'})")
                tools = await self._supports_tools(c, ref)
        except (httpx.HTTPError, RuntimeError, ValueError) as e:
            return ToolResult(ok=False, output="", error=str(e))
        out = f"pulled {ref}" + (f" ({_fmt(size)})" if size else "") + ". It is now in AON's model list. "
        out += ("tool calling: yes, it can run AON's agent loop." if tools else
                "tool calling: NO, it can chat but cannot drive AON's tools; use another model for agent work.")
        return ToolResult(ok=True, output=out)

    async def _supports_tools(self, c, ref) -> bool:
        r = await c.post(f"{self.root}/api/show", json={"model": ref})
        if r.status_code != 200:
            return False
        d = r.json()
        if "capabilities" in d:
            return "tools" in (d.get("capabilities") or [])
        return ".Tools" in (d.get("template") or "")


class ListModelsTool(_OllamaTool):
    name = "list_models"
    description = "List the models installed in Ollama (name, size, quantization)."
    parameters = {"type": "object", "properties": {}}
    mutating = False

    async def run(self, args):
        try:
            async with self._client() as c:
                r = await c.get(f"{self.root}/api/tags")
                r.raise_for_status()
                models = r.json().get("models", [])
        except (httpx.HTTPError, ValueError) as e:
            return ToolResult(ok=False, output="", error=str(e))
        lines = [f"- {m['name']}  {_fmt(m.get('size', 0))}  {(m.get('details') or {}).get('quantization_level', '')}"
                 for m in models]
        return ToolResult(ok=True, output="\n".join(lines) or "(no models)")


class RemoveModelTool(_OllamaTool):
    name = "remove_model"
    description = "Delete a model from Ollama to free disk space."
    parameters = {"type": "object", "properties": {"model": {"type": "string"}}, "required": ["model"]}
    mutating = True

    async def run(self, args):
        try:
            async with self._client() as c:
                r = await c.request("DELETE", f"{self.root}/api/delete", json={"model": args["model"]})
        except httpx.HTTPError as e:
            return ToolResult(ok=False, output="", error=str(e))
        if r.status_code >= 400:
            return ToolResult(ok=False, output="", error=f"Ollama delete failed: HTTP {r.status_code} {r.text[:120]}")
        return ToolResult(ok=True, output=f"removed {args['model']}")
