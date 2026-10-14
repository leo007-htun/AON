from __future__ import annotations
import json
import re as _re
from aon.tools.base import Tool, ToolResult

_INSTALL_PATS = [
    r"\bpip\s+install\b", r"\buv\s+pip\s+install\b", r"\bnpm\s+(i|install|add)\b",
    r"\b(yarn|pnpm|bun)\s+add\b", r"\bapt(-get)?\s+install\b", r"\bgo\s+install\b",
    r"\bcargo\s+install\b", r"\bgem\s+install\b", r"\bplaywright\s+install\b",
    r"\bmake\s+install\b", r"\b(tar|unzip)\b", r"\bbrew\s+install\b",
]


def is_install_command(cmd: str) -> bool:
    c = (cmd or "").lower()
    return any(_re.search(p, c) for p in _INSTALL_PATS)

_CONVO_RULE = (
    "Extract ONLY what the USER stated or confirmed about themselves, their plans, preferences or "
    "decisions, never things the assistant merely suggested, listed or asked about. Write each "
    "content as a standalone sentence (e.g. \"User's favorite color is teal\"). ")

_EXTRACT_SYS = (
    "Extract ONLY durable, reusable items worth remembering long-term: user preferences, "
    "decisions, stable facts, milestones, or reference knowledge. Skip greetings, transient "
    "state, and anything one-off. Return ONLY a JSON array of objects "
    "{\"type\":\"preference|decision|fact|milestone|reference\",\"content\":\"...\",\"tags\":\"space words\"}. "
    "If nothing is worth storing, return [].")


def _json_array(text):
    s = text.find("["); e = text.rfind("]")
    if s != -1 and e > s:
        return json.loads(text[s:e + 1])
    raise ValueError("no json array")


async def extract_episodes(text, llm, context="conversation"):
    try:
        sys = (_CONVO_RULE + _EXTRACT_SYS) if context == "conversation" else _EXTRACT_SYS
        res = await llm.chat([{"role": "system", "content": sys},
                              {"role": "user", "content": f"[{context}]\n{text}"}])
        arr = _json_array(res.content or "")
        out = []
        for e in arr:
            c = (e.get("content") or "").strip()
            if c:
                out.append({"type": e.get("type", "fact"), "content": c, "tags": e.get("tags", "")})
        return out
    except Exception:
        return []


async def maybe_extract(memory, llm, goal, final, source="llm"):
    try:
        eps = await extract_episodes(f"User: {goal}\nAssistant: {final}", llm)
        for e in eps:
            memory.add(e["content"], type=e["type"], tags=e.get("tags", ""), source=source)
    except Exception:
        pass


_PROC_HINTS = ("step 1", "step 2", "how-to", "how to", "procedure", "install", "run the", "npx ", "npm ")


class SearchMemoryTool(Tool):
    name = "search_memory"
    description = "Search long-term memory for episodes relevant to a query (older context not in the recent block)."
    parameters = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    mutating = False

    def __init__(self, memory, embedder=None):
        self.memory = memory
        self.embedder = embedder

    async def run(self, args):
        rows = []
        if self.embedder is not None:
            from aon.memory_semantic import search_episodes
            rows = await search_episodes(self.memory, self.embedder, args["query"], limit=8)
        if not rows:
            rows = self.memory.search(args["query"])
        if not rows:
            return ToolResult(ok=True, output="(no matching memory)")
        return ToolResult(ok=True, output="\n".join(f"- [{r['type']}] {r['content']}" for r in rows))


class SearchConversationTool(Tool):
    name = "search_conversation"
    description = ("Search THIS chat's earlier turns (beyond the recent window) for a topic; "
                   "all_chats=true searches the user's other chats too.")
    parameters = {"type": "object", "properties": {"query": {"type": "string"},
                                                   "all_chats": {"type": "boolean"}}, "required": ["query"]}
    mutating = False

    def __init__(self, db, convo, embedder=None):
        self.db = db
        self.convo = convo
        self.embedder = embedder

    async def run(self, args):
        from aon.recall import index_turns, recall, index_all, recall_other_chats
        from aon.context import clip_text
        rows = []
        if self.embedder is not None and args.get("all_chats"):
            await index_all(self.db, self.embedder, limit=256)
            rows = await recall_other_chats(self.db, self.embedder, self.convo, args["query"], budget=3000, k=8)
        if self.embedder is not None and not rows:
            # by meaning first; keyword matching when there are no vectors or the embedder is down
            await index_turns(self.db, self.embedder, self.convo)
            rows = await recall(self.db, self.embedder, self.convo, args["query"], exclude_ids=set(),
                                budget=3000, k=8)
        if not rows:
            rows = self.db.search_turns(self.convo, args["query"])
        if not rows:
            return ToolResult(ok=True, output="(no matching earlier turns)")
        return ToolResult(ok=True, output="\n".join(f"- {r['role']}: {clip_text(r['content'], 600)}" for r in rows))


class LearnUrlTool(Tool):
    name = "learn_url"
    description = ("Learn from a web page: fetch it, digest durable knowledge into memory, and for "
                   "how-to/procedural content, report that it should become a skill (then use write_skill).")
    parameters = {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}
    mutating = False

    def __init__(self, fetch, llm, memory):
        self.fetch = fetch
        self.llm = llm
        self.memory = memory

    async def run(self, args):
        try:
            text = await self.fetch(args["url"])
        except Exception as e:
            return ToolResult(ok=False, output="", error=f"fetch failed: {e}")
        text = (text or "")[:16000]
        eps = await extract_episodes(text, self.llm, context=f"web page {args['url']}")
        for e in eps:
            self.memory.add(e["content"], type=e["type"], tags=e.get("tags", ""),
                            source=f"url:{args['url'][:60]}")
        procedural = any(h in text.lower() for h in _PROC_HINTS)
        excerpt = " ".join(text.split())[:400]
        msg = f"Learned {len(eps)} item(s) from {args['url']}. Page: {excerpt}..."
        if procedural:
            msg += (f" This looks procedural/how-to — to turn it into a reusable skill, call "
                    f"draft_skill('{args['url']}') (it drafts the SKILL.md from this page).")
        return ToolResult(ok=True, output=msg)
