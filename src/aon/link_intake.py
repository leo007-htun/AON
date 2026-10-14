"""Link intake: when the user shares a URL, fetch and digest it, then ask what they want.

Turn 1 (message with a link): fetch the page, classify it (MCP server, skill repo, docs,
article...), and either act on an intent the user already stated or ask a follow-up
question whose options depend on the content. The offered options are stored per
conversation. Turn 2 (the reply): map "1" / "install it" / "summarize" to an option and
hand the engine a concrete goal for that intent, skipping the general router.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass
from pathlib import Path

OPTIONS = {
    "install_model": "Install this model into Ollama (GPU box)",
    "install_mcp": "Install it as an MCP server in AON",
    "import_skills": "Import its skills into AON",
    "summarize": "Just tell me what it is (summary)",
    "make_skill": "Turn it into an AON skill",
    "remember": "Save the key facts to memory",
}
_BY_KIND = {
    "gguf_model": ["install_model", "summarize"],
    "mcp_server": ["install_mcp", "summarize", "remember"],
    "skill_repo": ["import_skills", "summarize"],
    "docs": ["summarize", "make_skill", "remember"],
    "article": ["summarize", "remember"],
    "other": ["summarize", "remember"],
}
_KIND_LABEL = {"gguf_model": "a GGUF model", "mcp_server": "an MCP server", "skill_repo": "a skills repository",
               "docs": "documentation / a how-to", "article": "an article", "other": "a web page"}
# reply words -> option (checked in the order the options were offered)
_KEYWORDS = {
    "install_model": ("install", "pull", "download", "add it", "get it"),
    "install_mcp": ("install", "add it", "set up", "setup", "connect", "mcp"),
    "import_skills": ("import", "install", "add them", "add it"),
    "summarize": ("summar", "explain", "what is", "what it is", "tell me", "contents", "overview", "know"),
    "make_skill": ("skill",),
    "remember": ("remember", "memory", "save", "learn", "note"),
}
_NO = re.compile(r"^\s*(no|nope|nothing|skip|cancel|never ?mind|leave it)\b", re.I)
_URL = re.compile(r"https?://[^\s<>\"')\]]+")

_CLASSIFY_SYS = (
    "You classify a web page the user shared with an AI dev agent. Return ONLY a JSON object: "
    "{\"kind\":\"gguf_model|mcp_server|skill_repo|docs|article|other\",\"title\":\"short page title\","
    "\"summary\":\"2-3 plain sentences: what this is and what it is for\","
    "\"user_intent\":\"install_model|install_mcp|import_skills|summarize|make_skill|remember|null\"}. "
    "gguf_model = a model repository with GGUF weights (e.g. on Hugging Face) that can be run in Ollama. "
    "mcp_server = a Model Context Protocol server someone can connect to an agent. "
    "skill_repo = a repository of Agent Skills (folders with SKILL.md). "
    "user_intent = what the user's OWN message explicitly asks for; null if they only pasted "
    "the link or it is unclear.")


def find_urls(text: str) -> list[str]:
    return [u.rstrip(".,;:!?") for u in _URL.findall(text or "")]


def _npx_wrapper() -> str:
    p = Path(__file__).resolve().parents[2] / "bin" / "npx"
    return str(p) if p.exists() else "npx"


def mcp_playbook() -> str:
    return (
        "How to install an MCP server on this machine:\n"
        "- From the page, decide: local stdio server (command + args) or remote HTTP server "
        "(url + optional headers).\n"
        f"- Node is not on the system PATH. For npx-based servers use command `{_npx_wrapper()}` "
        "with args like `-y <package> ...`. Do not check for or install a system node/npx; the "
        "wrapper already provides them.\n"
        "- This machine has no display: browser servers need their headless flag (e.g. --headless). "
        "Google Chrome is not installed: use Playwright's Chromium (e.g. --browser=chromium; install it "
        f"once with `{_npx_wrapper()} -y playwright install chromium`).\n"
        "- If the server needs environment variables, create a small executable wrapper script "
        "that exports them and execs the server, and use that script as the command.\n"
        "- Verify prerequisites first (e.g. `which docker`), install missing ones with run_command.\n"
        "- If it needs an API key or an account, ask the user for it; never invent credentials.\n"
        "- Then call add_mcp_server. Report the resulting status and tools, or the exact error and "
        "what the user should do about it.")


@dataclass
class IntakeResult:
    ask: str | None = None      # follow-up question for the user
    goal: str | None = None     # concrete task for the engine
    intent: str = ""
    final: str | None = None    # reply without running the engine


class LinkIntake:
    def __init__(self, fetch, llm, store, convo: str):
        self.fetch = fetch      # async (url) -> readable text; raises on failure
        self.llm = llm
        self.store = store      # set/get/clear_pending_link
        self.convo = convo

    async def __call__(self, message: str, emit=None) -> IntakeResult | None:
        pending = self.store.get_pending_link(self.convo)
        if pending:
            self.store.clear_pending_link(self.convo)
            choice = self._match(message, pending["options"])
            if choice == "no":
                return IntakeResult(final="Okay, I'll leave that link alone.")
            if choice:
                return await self._resolve(choice, pending["url"])
        urls = find_urls(message)
        if not urls:
            return None
        url = urls[0]
        if emit:
            await emit({"type": "step", "tool": "web_fetch", "output": f"reading {url}", "approved": True})
        try:
            text = await self.fetch(url)
        except Exception:
            return None                 # let the normal agent deal with an unreachable link
        info = await self._classify(message, url, text)
        if info["user_intent"]:
            return self._goal(info["user_intent"], url, text)
        options = list(_BY_KIND.get(info["kind"], _BY_KIND["other"]))
        # offer what the page actually contains, whatever the model called it
        if _looks_mcp(url, text) and "install_mcp" not in options:
            options.insert(0, "install_mcp")
        if "skill.md" in text.lower() and "import_skills" not in options:
            options.insert(0, "import_skills")
        if _looks_gguf(url, text) and "install_model" not in options:
            options.insert(0, "install_model")
        self.store.set_pending_link(self.convo, {"url": url, "kind": info["kind"], "options": options})
        lines = [f"I read **{info['title'] or url}** ({url}).", "", info["summary"], "",
                 f"It looks like {_KIND_LABEL.get(info['kind'], 'a web page')}. What would you like to do?"]
        lines += [f"{i}. {OPTIONS[o]}" for i, o in enumerate(options, 1)]
        lines.append("\n(Reply with a number, or tell me something else.)")
        return IntakeResult(ask="\n".join(lines))

    async def _classify(self, message, url, text) -> dict:
        info = {"kind": "", "title": "", "summary": "", "user_intent": None}
        try:
            res = await self.llm.chat([
                {"role": "system", "content": _CLASSIFY_SYS},
                {"role": "user", "content": f"User message: {message}\nURL: {url}\n\nPage:\n{text[:6000]}"}])
            raw = res.content or ""
            d = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
            info.update({k: d.get(k) for k in info if d.get(k)})
        except Exception:
            pass
        if info["kind"] not in _BY_KIND:
            info["kind"] = _heuristic_kind(url, text)
        if info["user_intent"] not in OPTIONS:
            info["user_intent"] = None
        if not info["summary"]:
            info["summary"] = " ".join(text.split())[:300] + ("..." if len(text) > 300 else "")
        return info

    @staticmethod
    def _match(reply: str, options: list[str]):
        r = (reply or "").strip().lower()
        if _NO.match(r):
            return "no"
        m = re.match(r"^\s*(?:option\s*)?(\d+)\b", r)
        if m:
            i = int(m.group(1)) - 1
            return options[i] if 0 <= i < len(options) else None
        if find_urls(reply):
            return None                 # a new link starts a new intake
        for o in options:
            if any(k in r for k in _KEYWORDS[o]):
                return o
        return None

    async def _resolve(self, intent, url) -> IntakeResult:
        try:
            text = await self.fetch(url)
        except Exception as e:
            text = f"(could not re-fetch the page: {e})"
        return self._goal(intent, url, text)

    @staticmethod
    def _goal(intent, url, text) -> IntakeResult:
        page = f"\n\nPage content ({url}):\n{text[:8000]}"
        goals = {
            "summarize": (f"The user shared {url}. Explain what it is and summarize the key points "
                          "from the page content below. Answer directly; do not install anything." + page),
            "install_mcp": (f"Install the MCP server described at {url} into AON and connect it.\n\n"
                            + mcp_playbook() + page),
            "import_skills": (f"Import the skills from {url}: call import_skills with url={url}, then "
                              "tell the user which skills were installed or skipped and why."),
            "make_skill": (f"Create an AON skill from {url}: call draft_skill with url={url}, then "
                           "tell the user what was created."),
            "remember": (f"Learn from {url}: call learn_url with url={url}, then tell the user "
                         "briefly what was saved to memory."),
            "install_model": (f"Install the model from {url}: call pull_model with model={url} (it "
                              "picks a quantization that fits the GPU). Then tell the user the exact "
                              "model name pulled, its size, whether it supports tool calling, and that "
                              "it now appears in the model dropdown. If it failed, give the exact error."),
        }
        return IntakeResult(goal=goals[intent], intent=intent)


def _looks_mcp(url: str, text: str) -> bool:
    t = text.lower()
    return ("mcpservers" in t or "model context protocol" in t or "mcp server" in t
            or "/mcp" in url.lower())


def _looks_gguf(url: str, text: str) -> bool:
    u = url.lower()
    return ("huggingface.co/" in u or "hf.co/" in u) and ("gguf" in u or "gguf" in text.lower())


def _heuristic_kind(url: str, text: str) -> str:
    t = text.lower()
    if _looks_gguf(url, text):
        return "gguf_model"
    if "skill.md" in t:
        return "skill_repo"
    if _looks_mcp(url, text):
        return "mcp_server"
    if any(w in t for w in ("install", "getting started", "usage", "documentation", "api reference")):
        return "docs"
    return "article"


async def fetch_text(url: str, transport=None) -> str:
    """Readable page text; raises on HTTP errors so error URLs (401 API endpoints...) are not digested."""
    import httpx
    from aon.tools.web import _readable
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, transport=transport) as c:
        r = await c.get(url, headers={"User-Agent": "Mozilla/5.0 agent"})
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} from {url}")
    return _readable(r.text)
