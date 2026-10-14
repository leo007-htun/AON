import sys
from pathlib import Path
from fastapi import FastAPI
from agent.config import load_config
from agent.persistence import Database
from agent.web import create_app
from agent.build import build_registry, mem_store, skill_store, make_router
from agent.llm_client import LLMClient
from agent.engine import Agent
from agent.prompts import load_system_prompt
from agent.geo import build_map, default_geo
from agent.context import input_limit
from agent.recall import Embedder
from agent.memory_semantic import SemanticMemory
from agent.backends import make_chat_llm

def build_app_from_config(path: str) -> FastAPI:
    cfg = load_config(path)
    db = Database(str(cfg.workdir / "app.db"))
    from agent.mcp_client import McpManager
    from agent.memory_extract import SearchConversationTool
    from agent.link_intake import LinkIntake, fetch_text
    mcp = McpManager("mcp.json")
    embedder = Embedder(cfg.ollama_base_url, cfg.embed_model)
    from agent.docs import DocStore, SearchDocumentsTool, ListDocumentsTool, AddDocumentTool
    docs = DocStore(cfg.workdir / "docs.db")
    def make_agent(gate, on_event, model, convo="default"):
        reg = build_registry(cfg, mcp_manager=mcp, model=model, embedder=embedder)
        reg.register(SearchConversationTool(db, convo, embedder=embedder))
        reg.register(SearchDocumentsTool(docs, embedder))
        reg.register(ListDocumentsTool(docs))
        reg.register(AddDocumentTool(docs, embedder, cfg.workdir))
        llm = make_chat_llm(cfg, model)
        memory = SemanticMemory(mem_store(cfg), embedder)
        return Agent(llm, reg, gate, max_steps=cfg.max_steps, on_event=on_event,
                     memory=memory, skills=skill_store(cfg),
                     system_prompt=load_system_prompt(Path("system_prompt.txt")),
                     router=make_router(cfg, llm, reg, memory=memory),
                     intake=LinkIntake(fetch_text, llm, db, convo),
                     mapper=lambda spec, origin=None: build_map(spec, default_geo(), origin=origin),
                     context_limit=input_limit(cfg.context_window, cfg.reply_reserve))
    return create_app(cfg, db, make_agent, mcp_manager=mcp, embedder=embedder, docs=docs)

if __name__ == "__main__":
    import uvicorn
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.toml"
    app = build_app_from_config(cfg_path)
    host, _, port = load_config(cfg_path).bind.partition(":")
    # attachments arrive base64-encoded over the websocket (40 MB raw max, ~54 MB encoded)
    uvicorn.run(app, host=host, port=int(port or 8700), ws_max_size=64 * 1024 * 1024)
