"""AON as an MCP server (`aon mcp`, stdio).

Other AI apps (Claude Desktop, Claude Code, Cursor, ...) can hand AON a task, search its memory and
document library, and add to them. ask_aon runs the full agent with its model, tools and memory,
but anything that changes the machine (shell commands, file writes) is refused: in another app
there is nobody to approve it. Chats started here are kept like any other, under "mcp" or
"mcp-<chat>", so a client can continue them.
"""
from __future__ import annotations
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from aon.approval_gate import AutoGate
from aon.context import fit_history

INSTRUCTIONS = (
    "AON is the user's self-hosted AI agent. It has long-term memory about the user and a library of "
    "the user's documents. Use search_memory and search_documents to look things up, remember and "
    "add_document to save, and ask_aon to hand AON a whole task (it can research, use its tools and "
    "answer from its memory). AON refuses shell commands and file writes from here."
)


class AonService:
    """The work behind each MCP tool, kept separate from the MCP layer so it can be tested."""

    def __init__(self, cfg, db, docs, mem, embedder, make_agent):
        self.cfg, self.db, self.docs, self.mem, self.embedder, self.make_agent = cfg, db, docs, mem, embedder, make_agent

    async def ask(self, goal: str, chat: str | None = None) -> str:
        convo = f"mcp-{chat}" if chat else "mcp"
        try:
            sent = fit_history(self.db.recent_turn_rows(convo, 20), self.cfg.history_tokens)
        except Exception:
            sent = []
        history = [{"role": t["role"], "content": t["content"]} for t in sent]
        agent = self.make_agent(AutoGate(approve=False), None, self.cfg.default_model, convo)
        res = await agent.run(goal, conversation=history)
        final = (getattr(res, "final", "") or "").strip()
        self.db.add_turn(convo, "user", goal)
        self.db.add_turn(convo, "assistant", final)
        if getattr(res, "status", "done") not in ("done", "clarify"):
            final = f"[{res.status}] {final}"
        return final

    async def search_memory(self, query: str) -> str:
        from aon.memory_extract import SearchMemoryTool
        return (await SearchMemoryTool(self.mem, embedder=self.embedder).run({"query": query})).output

    async def remember(self, fact: str, kind: str = "fact") -> str:
        from aon.memory import RememberTool
        return (await RememberTool(self.mem).run({"content": fact, "type": kind})).output

    async def search_documents(self, query: str, limit: int = 5) -> str:
        from aon.docs import SearchDocumentsTool
        return (await SearchDocumentsTool(self.docs, self.embedder).run({"query": query, "limit": limit})).output

    async def list_documents(self) -> str:
        from aon.docs import ListDocumentsTool
        return (await ListDocumentsTool(self.docs).run({})).output

    async def add_document(self, name: str, text: str) -> str:
        from aon.docs import ingest, DocError
        try:
            d = await ingest(self.docs, self.embedder, name, (text or "").encode("utf-8"), source="mcp")
        except DocError as e:
            return f"Could not add {name}: {e}"
        if d.get("existing"):
            return f"{d['name']} is already in the library."
        n = d["chunks"]
        return f"Added {d['name']} to the library ({n} passage{'' if n == 1 else 's'})."


def build_server(service: AonService) -> FastMCP:
    server = FastMCP("aon", instructions=INSTRUCTIONS, website_url="https://github.com/leo007-htun/AON")
    read_only = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    writes_aon = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                                 openWorldHint=False)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
    async def ask_aon(goal: str, chat: str | None = None) -> str:
        """Give AON a task or question. It answers with its own model, memory, documents and tools
        (web search and fetch, maps, reading files). Shell commands and file writes are refused.
        Use the same `chat` name to continue a conversation; leave it empty for the default chat."""
        return await service.ask(goal, chat)

    @server.tool(annotations=read_only)
    async def search_memory(query: str) -> str:
        """Search AON's long-term memory about the user (facts, preferences, decisions) by meaning."""
        return await service.search_memory(query)

    @server.tool(annotations=writes_aon)
    async def remember(fact: str, kind: str = "fact") -> str:
        """Save a durable fact about the user to AON's memory. kind: fact, preference, decision,
        milestone, action or reference. Secrets such as tokens and keys are refused."""
        return await service.remember(fact, kind)

    @server.tool(annotations=read_only)
    async def search_documents(query: str, limit: int = 5) -> str:
        """Search the user's document library by meaning and exact words; returns the best passages
        with their document names."""
        return await service.search_documents(query, limit)

    @server.tool(annotations=read_only)
    async def list_documents() -> str:
        """List the documents in the user's library."""
        return await service.list_documents()

    @server.tool(annotations=writes_aon)
    async def add_document(name: str, text: str) -> str:
        """Add a text document to the user's library so AON (and search_documents) can use it.
        `name` is shown in citations, e.g. "meeting-notes.md"."""
        return await service.add_document(name, text)

    return server


def build_service(config_path) -> AonService:
    from aon.main import build_components
    from aon.build import mem_store
    c = build_components(config_path)
    return AonService(c.cfg, c.db, c.docs, mem_store(c.cfg), c.embedder, c.make_agent)


def quiet_logs() -> None:
    """One log line per model or embedding request floods an MCP client's log; keep warnings only."""
    import logging
    for name in ("httpx", "httpcore", "mcp.server.lowlevel.server"):
        logging.getLogger(name).setLevel(logging.WARNING)


def run_stdio(config_path) -> None:
    server = build_server(build_service(config_path))
    quiet_logs()
    server.run("stdio")
