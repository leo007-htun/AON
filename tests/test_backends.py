"""Chat requests go to the configured chat backend (vLLM) with its sampling; embeddings and
model-management stay on Ollama. With no chat backend configured, everything stays on Ollama."""
import httpx
import pytest
from agent.config import Config, load_config
from agent.backends import chat_base_url, chat_sampling, make_chat_llm, model_is_chat
from agent.llm_client import LLMClient


def _cfg(tmp, **kw):
    base = dict(ollama_base_url="http://ol/v1", default_model="aon", workdir=tmp, always_ask=[],
                max_steps=5, command_timeout=10, bind="127.0.0.1:8700")
    base.update(kw)
    return Config(**base)


def test_defaults_keep_everything_on_ollama(tmp_path):
    c = _cfg(tmp_path, default_model="gpt-oss")
    assert c.chat_base_url == "" and c.chat_model == ""
    assert chat_base_url(c, "gpt-oss") == "http://ol/v1"
    assert chat_sampling(c, "gpt-oss") == {}
    assert model_is_chat(c, "gpt-oss") is False


def test_chat_model_routes_to_the_chat_backend(tmp_path):
    c = _cfg(tmp_path, chat_base_url="http://vllm:8001/v1", chat_model="aon",
             chat_temperature=0.2, chat_repetition_penalty=1.15)
    assert model_is_chat(c, "aon") is True
    assert chat_base_url(c, "aon") == "http://vllm:8001/v1"
    assert chat_sampling(c, "aon") == {"temperature": 0.2, "repetition_penalty": 1.15}


def test_none_model_uses_the_default(tmp_path):
    c = _cfg(tmp_path, chat_base_url="http://vllm:8001/v1", chat_model="aon", default_model="aon")
    assert chat_base_url(c, None) == "http://vllm:8001/v1"
    assert model_is_chat(c, None) is True


def test_other_models_stay_on_ollama_even_with_a_chat_backend(tmp_path):
    c = _cfg(tmp_path, chat_base_url="http://vllm:8001/v1", chat_model="aon", chat_temperature=0.2)
    assert chat_base_url(c, "gpt-oss") == "http://ol/v1"
    assert chat_sampling(c, "gpt-oss") == {}
    assert model_is_chat(c, "gpt-oss") is False


def test_chat_base_url_falls_back_to_ollama_when_blank(tmp_path):
    c = _cfg(tmp_path, chat_model="aon")      # chat_model set but no url given
    assert chat_base_url(c, "aon") == "http://ol/v1"


def test_make_chat_llm_wires_url_model_and_sampling(tmp_path):
    c = _cfg(tmp_path, chat_base_url="http://vllm:8001/v1", chat_model="aon",
             chat_temperature=0.2, chat_repetition_penalty=1.15)
    llm = make_chat_llm(c, "aon")
    assert llm.base_url == "http://vllm:8001/v1" and llm.model == "aon"
    assert llm.sampling == {"temperature": 0.2, "repetition_penalty": 1.15}
    tool = make_chat_llm(c, None)
    assert tool.model == "aon"


def test_config_reads_chat_backend(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('ollama_base_url="http://ol/v1"\ndefault_model="aon"\nworkdir="."\n'
                 'chat_base_url="http://vllm:8001/v1"\nchat_model="aon"\n'
                 'chat_temperature=0.2\nchat_repetition_penalty=1.15\nchat_vision=true\n')
    c = load_config(p)
    assert (c.chat_base_url, c.chat_model, c.chat_temperature, c.chat_repetition_penalty, c.chat_vision) == \
           ("http://vllm:8001/v1", "aon", 0.2, 1.15, True)


@pytest.mark.asyncio
async def test_llm_client_sends_sampling_in_the_payload():
    seen = {}
    def handler(request):
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok", "tool_calls": None}}]})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = LLMClient("http://vllm/v1", "aon", http=http, sampling={"temperature": 0.2, "repetition_penalty": 1.15})
    await llm.chat([{"role": "user", "content": "hi"}])
    assert seen["body"]["temperature"] == 0.2 and seen["body"]["repetition_penalty"] == 1.15
    assert seen["body"]["model"] == "aon"


@pytest.mark.asyncio
async def test_llm_client_without_sampling_sends_none():
    seen = {}
    def handler(request):
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await LLMClient("http://ol/v1", "m", http=http).chat([{"role": "user", "content": "hi"}])
    assert "temperature" not in seen["body"] and "repetition_penalty" not in seen["body"]


def test_api_models_lists_the_chat_backend(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from agent.persistence import Database
    from agent.web import create_app
    import agent.web as W
    async def fake_list(base_url, model):
        return ["aon"] if "vllm" in base_url else ["gpt-oss", "nomic-embed-text"]
    monkeypatch.setattr(W, "_list_models", fake_list)
    cfg = _cfg(tmp_path, chat_base_url="http://vllm:8001/v1", chat_model="aon")
    app = create_app(cfg, Database(str(tmp_path / "d.db")), lambda *a, **k: None, mem_path=tmp_path / "m.db")
    with TestClient(app) as c:
        assert c.get("/api/models").json() == {"models": ["aon"]}


@pytest.mark.asyncio
async def test_chat_model_image_uses_configured_vision(tmp_path):
    # the chat backend model is not an Ollama model, so its vision support comes from config,
    # not an Ollama /api/show probe
    from agent.backends import model_is_chat
    c = _cfg(tmp_path, chat_base_url="http://vllm:8001/v1", chat_model="aon", chat_vision=True)
    assert model_is_chat(c, "aon") and c.chat_vision is True
