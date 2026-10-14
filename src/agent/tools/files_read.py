from pathlib import Path
from agent.tools.base import Tool, ToolResult
from agent.tools.paths import resolve_in_root, PATH_PARAM

class ReadFileTool(Tool):
    name = "read_file"; description = "Read a UTF-8 text file within the workdir."
    parameters = {"type": "object", "properties": {"path": PATH_PARAM}, "required": ["path"]}
    mutating = False
    def __init__(self, root: Path): self.root = Path(root)
    async def run(self, args: dict) -> ToolResult:
        try:
            p = resolve_in_root(self.root, args["path"])
            return ToolResult(ok=True, output=p.read_text())
        except ValueError as e:
            return ToolResult(ok=False, output="", error=str(e))
        except OSError as e:
            return ToolResult(ok=False, output="", error=f"read failed: {e}")

class ListDirTool(Tool):
    name = "list_dir"; description = "List entries of a directory within the workdir."
    parameters = {"type": "object", "properties": {"path": PATH_PARAM}, "required": ["path"]}
    mutating = False
    def __init__(self, root: Path): self.root = Path(root)
    async def run(self, args: dict) -> ToolResult:
        try:
            p = resolve_in_root(self.root, args["path"])
            names = sorted((e.name + ("/" if e.is_dir() else "")) for e in p.iterdir())
            return ToolResult(ok=True, output="\n".join(names))
        except ValueError as e:
            return ToolResult(ok=False, output="", error=str(e))
        except OSError as e:
            return ToolResult(ok=False, output="", error=f"list failed: {e}")
