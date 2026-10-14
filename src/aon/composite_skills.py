from __future__ import annotations


class CompositeSkills:
    def __init__(self, skill_store, mcp_manager=None):
        self.store = skill_store
        self.mcp = mcp_manager

    def list(self):
        out = list(self.store.list())
        if self.mcp:
            for p in self.mcp.prompts():
                out.append((f"mcp:{p.server}:{p.name}", p.description))
        return out

    def items(self):
        rows = [{"name": n, "desc": d, "source": "file"} for n, d in self.store.list()]
        if self.mcp:
            for p in self.mcp.prompts():
                rows.append({"name": f"mcp:{p.server}:{p.name}", "desc": p.description, "source": "mcp"})
        return rows

    def read(self, name, path="SKILL.md"):
        return self.store.read(name, path)

    async def aread(self, name):
        if name.startswith("mcp:"):
            _, server, pname = name.split(":", 2)
            return await self.mcp.get_prompt(server, pname)
        return self.store.read(name)
