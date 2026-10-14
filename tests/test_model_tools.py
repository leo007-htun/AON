import json
import pytest
import httpx
from agent.model_tools import (parse_model_ref, pick_quant, ollama_root, PullModelTool,
                               ListModelsTool, RemoveModelTool)

GB = 1024 ** 3


def test_ollama_root():
    assert ollama_root("http://localhost:11434/v1") == "http://localhost:11434"
    assert ollama_root("http://h:11434/v1/") == "http://h:11434"
    assert ollama_root("http://h:11434") == "http://h:11434"


@pytest.mark.parametrize("ref,expected", [
    ("https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF", ("hf", "bartowski/Qwen2.5-7B-Instruct-GGUF", None)),
    ("https://huggingface.co/u/r-GGUF/blob/main/r.Q8_0.gguf", ("hf", "u/r-GGUF", "Q8_0")),
    ("https://huggingface.co/u/r-GGUF/tree/main", ("hf", "u/r-GGUF", None)),
    ("hf.co/u/r-GGUF:Q4_K_M", ("hf", "u/r-GGUF", "Q4_K_M")),
    ("huggingface.co/u/r-GGUF", ("hf", "u/r-GGUF", None)),
    ("llama3.2", ("ollama", "llama3.2", None)),
    ("qwen3:8b", ("ollama", "qwen3:8b", None)),
])
def test_parse_model_ref(ref, expected):
    assert parse_model_ref(ref) == expected


def test_pick_quant_prefers_q4_k_m_and_respects_budget():
    sizes = {"Q8_0": 30 * GB, "Q4_K_M": 18 * GB, "Q3_K_M": 14 * GB, "IQ2_XS": 9 * GB}
    assert pick_quant(sizes, 22 * GB) == "Q4_K_M"
    assert pick_quant(sizes, 16 * GB) == "Q3_K_M"
    assert pick_quant(sizes, 5 * GB) is None
    assert pick_quant({"Q5_K_M": 6 * GB, "Q4_0": 4 * GB}, 22 * GB) == "Q5_K_M"


def _transport(tree, pull_lines, caps=("completion", "tools"), seen=None):
    seen = seen if seen is not None else {}
    def handler(req: httpx.Request):
        if req.url.host == "huggingface.co":
            seen["hf"] = str(req.url)
            return httpx.Response(200, json=tree)
        if req.url.path == "/api/pull":
            seen["pull"] = json.loads(req.content)
            return httpx.Response(200, text="\n".join(json.dumps(l) for l in pull_lines))
        if req.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": list(caps)})
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": "gpt-oss:latest", "size": 13 * GB, "details": {"quantization_level": "MXFP4"}}]})
        if req.url.path == "/api/delete":
            seen["delete"] = json.loads(req.content)
            return httpx.Response(200)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


TREE = [{"type": "file", "path": "README.md", "size": 10},
        {"type": "file", "path": "Model-Q8_0.gguf", "size": 30 * GB},
        {"type": "file", "path": "Model-Q4_K_M.gguf", "size": 18 * GB},
        {"type": "file", "path": "Model-Q3_K_M.gguf", "size": 14 * GB}]
OK_PULL = [{"status": "pulling manifest"}, {"status": "downloading", "total": 100, "completed": 100},
           {"status": "success"}]


@pytest.mark.asyncio
async def test_pull_hf_picks_fitting_quant_and_reports_tools():
    seen = {}
    t = PullModelTool("http://localhost:11434/v1", max_gb=22, transport=_transport(TREE, OK_PULL, seen=seen))
    assert t.mutating is True
    r = await t.run({"model": "https://huggingface.co/u/Model-GGUF"})
    assert r.ok, r.error
    assert seen["pull"]["model"] == "hf.co/u/Model-GGUF:Q4_K_M"
    assert "hf.co/u/Model-GGUF:Q4_K_M" in r.output and "18.0 GB" in r.output
    assert "tool calling: yes" in r.output


@pytest.mark.asyncio
async def test_pull_warns_when_model_cannot_use_tools():
    t = PullModelTool("http://x:11434/v1", transport=_transport(TREE, OK_PULL, caps=("completion",)))
    r = await t.run({"model": "hf.co/u/Model-GGUF:Q3_K_M"})
    assert r.ok and "tool calling: NO" in r.output


@pytest.mark.asyncio
async def test_pull_refuses_too_big_unless_forced():
    tree = [{"type": "file", "path": "Big-Q4_K_M.gguf", "size": 40 * GB}]
    t = PullModelTool("http://x:11434/v1", max_gb=22, transport=_transport(tree, OK_PULL))
    r = await t.run({"model": "https://huggingface.co/u/Big-GGUF"})
    assert not r.ok and "22" in r.error and "40.0 GB" in r.error
    r = await t.run({"model": "hf.co/u/Big-GGUF:Q4_K_M", "force": True})
    assert r.ok


@pytest.mark.asyncio
async def test_pull_reports_ollama_error():
    t = PullModelTool("http://x:11434/v1", transport=_transport(TREE, [{"error": "file does not exist"}]))
    r = await t.run({"model": "llama9"})
    assert not r.ok and "file does not exist" in r.error


@pytest.mark.asyncio
async def test_pull_repo_without_gguf():
    t = PullModelTool("http://x:11434/v1", transport=_transport([{"type": "file", "path": "model.safetensors", "size": 5}], OK_PULL))
    r = await t.run({"model": "https://huggingface.co/u/not-gguf"})
    assert not r.ok and "GGUF" in r.error


@pytest.mark.asyncio
async def test_list_and_remove():
    seen = {}
    tr = _transport(TREE, OK_PULL, seen=seen)
    r = await ListModelsTool("http://x:11434/v1", transport=tr).run({})
    assert r.ok and "gpt-oss:latest" in r.output and "13.0 GB" in r.output
    rm = RemoveModelTool("http://x:11434/v1", transport=tr)
    assert rm.mutating is True
    r = await rm.run({"model": "gpt-oss:latest"})
    assert r.ok and seen["delete"] == {"model": "gpt-oss:latest"}


# ---- link intake ----

@pytest.mark.asyncio
async def test_link_intake_offers_install_model(tmp_path):
    from agent.link_intake import LinkIntake, OPTIONS
    from agent.persistence import Database
    from agent.llm_client import ChatResult
    class L:
        async def chat(self, m, tools=None):
            return ChatResult(json.dumps({"kind": "docs", "title": "Qwen GGUF", "summary": "s", "user_intent": None}), [])
    async def fetch(url): return "GGUF quantized files: Q4_K_M, Q8_0. Use with llama.cpp or Ollama."
    it = LinkIntake(fetch, L(), Database(str(tmp_path / "d.db")), "c")
    r = await it("https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF")
    assert "1. " + OPTIONS["install_model"] in r.ask
    r = await it("install it")
    assert r.intent == "install_model" and "pull_model" in r.goal
    assert "https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF" in r.goal


@pytest.mark.asyncio
async def test_hf_sizes_keeps_variants_apart_and_sums_only_shards():
    tree = [{"type": "file", "path": "M-Q4_0.gguf", "size": 4 * GB},
            {"type": "file", "path": "M-Q4_0_4_4.gguf", "size": 4 * GB},
            {"type": "file", "path": "M-Q4_0_8_8.gguf", "size": 4 * GB},
            {"type": "file", "path": "Q8_0/M-Q8_0-00001-of-00002.gguf", "size": 10 * GB},
            {"type": "file", "path": "Q8_0/M-Q8_0-00002-of-00002.gguf", "size": 5 * GB},
            {"type": "file", "path": "mmproj-M-F16.gguf", "size": 1 * GB}]
    t = PullModelTool("http://x:11434/v1", transport=_transport(tree, OK_PULL))
    async with t._client() as c:
        sizes = await t._hf_sizes(c, "u/M-GGUF")
    assert sizes["Q4_0"] == 4 * GB and sizes["Q4_0_4_4"] == 4 * GB and sizes["Q8_0"] == 15 * GB
    assert "F16" not in sizes
