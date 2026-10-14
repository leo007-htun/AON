import sys
from pathlib import Path
from fastapi import FastAPI
from aon.config import load_config
from aon.persistence import Database
from aon.web import create_app
from aon.build import build_registry, mem_store, skill_store, make_router
from aon.llm_client import LLMClient
from aon.engine import Agent
from aon.prompts import load_system_prompt
from aon.geo import build_map, default_geo
from aon.context import input_limit
from aon.recall import Embedder
from aon.memory_semantic import SemanticMemory
from aon.backends import make_chat_llm

class Components:
    """Everything AON is built from, shared by the web app (aon serve) and the MCP server (aon mcp)."""
    def __init__(self, cfg, db, mcp, embedder, docs, make_agent):
        self.cfg, self.db, self.mcp, self.embedder, self.docs, self.make_agent = cfg, db, mcp, embedder, docs, make_agent


def build_components(path) -> Components:
    cfg = load_config(path)
    db = Database(str(cfg.workdir / "app.db"))
    from aon.mcp_client import McpManager
    from aon.memory_extract import SearchConversationTool
    from aon.link_intake import LinkIntake, fetch_text
    mcp = McpManager("mcp.json")
    embedder = Embedder(cfg.ollama_base_url, cfg.embed_model)
    from aon.docs import DocStore, SearchDocumentsTool, ListDocumentsTool, AddDocumentTool
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
    return Components(cfg, db, mcp, embedder, docs, make_agent)


def build_app_from_config(path: str) -> FastAPI:
    c = build_components(path)
    return create_app(c.cfg, c.db, c.make_agent, mcp_manager=c.mcp, embedder=c.embedder, docs=c.docs)

if __name__ == "__main__":
    import uvicorn
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.toml"
    app = build_app_from_config(cfg_path)
    host, _, port = load_config(cfg_path).bind.partition(":")
    # attachments arrive base64-encoded over the websocket (40 MB raw max, ~54 MB encoded)
    uvicorn.run(app, host=host, port=int(port or 8700), ws_max_size=64 * 1024 * 1024)
