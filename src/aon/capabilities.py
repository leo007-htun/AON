from __future__ import annotations
from aon.tools.base import Tool, ToolResult


class CapabilitiesTool(Tool):
    name = "capabilities"
    description = "List what AON can do: its tools and its known skills (with any prerequisites)."
    parameters = {"type": "object", "properties": {}}
    mutating = False

    def __init__(self, registry, skill_store):
        self.registry = registry
        self.skills = skill_store

    async def run(self, args):
        tools = "\n".join(
            f"- {t.name}: {((t.description or '').splitlines() or [''])[0][:100]}"
            for t in self.registry.list() if t.name != self.name)
        sk = []
        for n, d in self.skills.list():
            try:
                req = self.skills.info(n).get("requires", "")
            except Exception:
                req = ""
            sk.append(f"- {n}: {d}" + (f" (requires: {req})" if req else ""))
        return ToolResult(ok=True, output=(
            "My capabilities (tools):\n" + tools +
            "\n\nMy skills:\n" + ("\n".join(sk) if sk else "(none yet)")))
