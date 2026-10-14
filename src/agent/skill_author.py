from __future__ import annotations
import re
from agent.tools.base import Tool, ToolResult
from agent.skills import parse_frontmatter

_DRAFT_SYS = (
    "You write Agent Skills. Given web page CONTENT, produce a single SKILL.md that captures the "
    "reusable knowledge or procedure as guidance for an agent. Output ONLY the file, starting with "
    "a YAML frontmatter block:\n---\nname: <kebab-case>\ndescription: <what it does AND when to use it>\n---\n"
    "then concise, stepwise markdown (add a Gotchas section if relevant). Do not wrap it in code fences.")


def _slug(url):
    tail = re.sub(r"[?#].*$", "", url).rstrip("/").split("/")[-1] or "learned-skill"
    tail = re.sub(r"[^a-z0-9]+", "-", tail.lower()).strip("-")
    return tail[:60] or "learned-skill"


def _strip_fences(md):
    md = (md or "").strip()
    if md.startswith("```"):
        lines = md.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        md = "\n".join(lines).strip()
    return md


async def draft_skill_md(content, url, llm, name_hint=None):
    res = await llm.chat([{"role": "system", "content": _DRAFT_SYS},
                          {"role": "user", "content": f"URL: {url}\nCONTENT:\n{(content or '')[:12000]}"}])
    md = _strip_fences(res.content or "")
    meta, body = parse_frontmatter(md)
    raw_name = name_hint or meta.get("name") or _slug(url)
    name = re.sub(r"[^a-z0-9-]+", "-", raw_name.lower()).strip("-") or _slug(url)
    body_src = body if (meta and body.strip()) else md
    first = next((l.strip() for l in body_src.splitlines() if l.strip() and l.strip() != "---"), "")
    desc = (meta.get("description") or first or f"Knowledge learned from {url}").strip()[:1000]
    md_out = f"---\nname: {name}\ndescription: {desc}\n---\n\n{body_src.strip()}"
    return name, md_out


class DraftSkillTool(Tool):
    name = "draft_skill"
    description = ("Create a reusable skill FROM a web page: fetch it and draft a grounded SKILL.md, then save it. "
                   "Use when asked to learn a URL/doc as a skill or turn a page into a skill.")
    parameters = {"type": "object", "properties": {
        "url": {"type": "string"}, "name": {"type": "string"}}, "required": ["url"]}
    mutating = False

    def __init__(self, fetch, llm, store):
        self.fetch = fetch
        self.llm = llm
        self.store = store

    async def run(self, args):
        try:
            text = await self.fetch(args["url"])
        except Exception as e:
            return ToolResult(ok=False, output="", error=f"fetch failed: {e}")
        if not (text or "").strip():
            return ToolResult(ok=False, output="", error="page was empty")
        name, md = await draft_skill_md(text, args["url"], self.llm, args.get("name"))
        try:
            self.store.write(name, md)
        except (ValueError, OSError) as e:
            return ToolResult(ok=False, output="", error=str(e))
        return ToolResult(ok=True, output=(
            f"Created skill '{name}' from {args['url']} "
            f"(files: {', '.join(self.store.files(name))}). Review it, then run_skill_evals('{name}')."))
