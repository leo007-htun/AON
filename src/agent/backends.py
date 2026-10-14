"""Which backend serves a chat request.

AON can keep one chat model on a separate backend (vLLM, for real multi-user serving) while
everything else - the embedder and the model-management tools - stays on Ollama. The chat model
is named by cfg.chat_model; requests for it (or for the default when it is the chat model) go to
cfg.chat_base_url with the chat sampling settings. Any other model stays on Ollama. With
cfg.chat_model blank, nothing changes: all chat is on Ollama as before.
"""
from __future__ import annotations


def model_is_chat(cfg, model) -> bool:
    """True when this request should go to the separate chat backend."""
    if not cfg.chat_model:
        return False
    return (model or cfg.default_model) == cfg.chat_model


def chat_base_url(cfg, model=None) -> str:
    if model_is_chat(cfg, model):
        return cfg.chat_base_url or cfg.ollama_base_url
    return cfg.ollama_base_url


def chat_sampling(cfg, model=None) -> dict:
    if not model_is_chat(cfg, model):
        return {}
    out = {}
    if cfg.chat_temperature is not None:
        out["temperature"] = cfg.chat_temperature
    if cfg.chat_repetition_penalty is not None:
        out["repetition_penalty"] = cfg.chat_repetition_penalty
    if cfg.chat_top_p is not None:
        out["top_p"] = cfg.chat_top_p
    return out


def make_chat_llm(cfg, model, http=None):
    from agent.llm_client import LLMClient
    return LLMClient(chat_base_url(cfg, model), model or cfg.default_model,
                     http=http, sampling=chat_sampling(cfg, model),
                     retry_without_thinking=model_is_chat(cfg, model) and bool(cfg.chat_base_url))
