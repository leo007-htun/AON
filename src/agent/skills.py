from __future__ import annotations
import re
from pathlib import Path
from agent.tools.base import Tool, ToolResult
from agent.tools.paths import resolve_in_root

DESCRIPTION_MAX = 1024

SKILL_FORMAT = (
    "A skill is a folder containing a SKILL.md file (the Agent Skills format). "
    "SKILL.md starts with YAML frontmatter and then the instructions:\n"
    "---\n"
    "name: <kebab-case name, matches the folder>\n"
    "description: <one line: what it does and WHEN to use it>\n"
    "---\n\n"
    "<markdown instructions the agent follows when the skill activates>"
)


def validate_description(desc: str):
    if desc and len(desc) > DESCRIPTION_MAX:
        return f"description must be <= {DESCRIPTION_MAX} chars (got {len(desc)})"
    return None


def parse_frontmatter(text: str):
    """Return (meta_dict, body). Supports a leading --- ... --- YAML block (simple key: value)."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    meta, block = {}, None
    for line in m.group(1).splitlines():
        if block and (line.startswith((" ", "\t")) or not line.strip()):
            meta[block] = (meta[block] + " " + line.strip()).strip()
            continue
        block = None
        if ":" in line and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip()
            if v in (">", "|", ">-", "|-"):
                block, v = k, ""
            elif len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            meta[k] = v
    return meta, m.group(2)


def _first_line(text: str) -> str:
    for ln in text.splitlines():
        s = ln.strip()
        if s and s != "---":
            return s
    return ""


class SkillStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _safe(self, name: str) -> Path:
        if "/" in name or "\\" in name or ".." in name or name.startswith("."):
            raise ValueError(f"invalid skill name {name!r}")
        return self.root / name

    def _in_skill(self, name: str, rel: str) -> Path:
        return resolve_in_root(self._safe(name), rel or "SKILL.md")

    def list(self) -> list[tuple[str, str]]:
        if not self.root.exists():
            return []
        out = []
        for d in sorted(self.root.iterdir()):
            f = d / "SKILL.md"
            if d.is_dir() and f.exists():
                meta, body = parse_frontmatter(f.read_text())
                out.append((meta.get("name") or d.name, meta.get("description") or _first_line(body)))
        return out

    def read(self, name: str, rel: str = "SKILL.md") -> str:
        return self._in_skill(name, rel).read_text()

    def write(self, name: str, content: str, rel: str = "SKILL.md") -> None:
        target = self._in_skill(name, rel)
        if (rel or "SKILL.md") == "SKILL.md":
            meta, body = parse_frontmatter(content)
            err = validate_description(meta.get("description", ""))
            if err:
                raise ValueError(err)
            if "name" not in meta or "description" not in meta:
                nm = meta.get("name", name)
                desc = meta.get("description") or _first_line(body or content)
                err = validate_description(desc)
                if err:
                    raise ValueError(err)
                body_text = body if meta else content
                content = f"---\nname: {nm}\ndescription: {desc}\n---\n\n{body_text.lstrip()}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def files(self, name: str) -> list[str]:
        base = self._safe(name)
        if not base.exists():
            return []
        return sorted(str(p.relative_to(base)).replace("\\", "/")
                      for p in base.rglob("*") if p.is_file())

    def info(self, name: str) -> dict:
        meta, _ = parse_frontmatter(self.read(name))
        return {"name": meta.get("name", name), "description": meta.get("description", ""),
                "requires": meta.get("requires", ""), "files": self.files(name)}


class ReadSkillTool(Tool):
    name = "read_skill"
    description = "Read a file inside a skill folder (default SKILL.md). Use for references/ and scripts/ too."
    parameters = {"type": "object", "properties": {
        "name": {"type": "string"},
        "path": {"type": "string", "description": "relative path in the skill, default SKILL.md"}},
        "required": ["name"]}
    mutating = False

    def __init__(self, store: SkillStore):
        self.store = store

    async def run(self, args):
        try:
            return ToolResult(ok=True, output=self.store.read(args["name"], args.get("path", "SKILL.md")))
        except (ValueError, OSError) as e:
            return ToolResult(ok=False, output="", error=str(e))


class WriteSkillTool(Tool):
    name = "write_skill"
    description = (
        "Create or update a file inside a skill (Agent Skills format). Default path is "
        "SKILL.md (YAML frontmatter name+description, then markdown). Use path= to add "
        "references/*.md, scripts/*, assets/*, or evals/evals.json.\n" + SKILL_FORMAT)
    parameters = {"type": "object", "properties": {
        "name": {"type": "string"}, "content": {"type": "string"},
        "path": {"type": "string", "description": "relative path in the skill, default SKILL.md"}},
        "required": ["name", "content"]}
    mutating = False

    def __init__(self, store: SkillStore):
        self.store = store

    async def run(self, args):
        try:
            self.store.write(args["name"], args["content"], args.get("path", "SKILL.md"))
            return ToolResult(ok=True, output=f"saved {args.get('path', 'SKILL.md')} in skill '{args['name']}'")
        except (ValueError, OSError) as e:
            return ToolResult(ok=False, output="", error=str(e))


class SkillFilesTool(Tool):
    name = "skill_files"
    description = "List the files inside a skill folder."
    parameters = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    mutating = False

    def __init__(self, store: SkillStore):
        self.store = store

    async def run(self, args):
        try:
            return ToolResult(ok=True, output="\n".join(self.store.files(args["name"])) or "(empty)")
        except (ValueError, OSError) as e:
            return ToolResult(ok=False, output="", error=str(e))
