from pathlib import Path
from aon.tools.base import Tool, ToolResult
from aon.tools.paths import resolve_in_root, PATH_PARAM
from aon.config import GUARDRAIL_PATHS

def is_guardrail(root: Path, abspath: Path) -> bool:
    root = root.resolve()
    for g in GUARDRAIL_PATHS:
        if abspath.resolve() == (root / g).resolve():
            return True
    return False

class WriteFileTool(Tool):
    name = "write_file"; description = "Create or overwrite a text file within the workdir."
    parameters = {"type": "object",
                  "properties": {"path": PATH_PARAM, "content": {"type": "string"}},
                  "required": ["path", "content"]}
    mutating = True
    def __init__(self, root: Path): self.root = Path(root)
    async def run(self, args: dict) -> ToolResult:
        try:
            p = resolve_in_root(self.root, args["path"])
        except ValueError as e:
            return ToolResult(ok=False, output="", error=str(e))
        if is_guardrail(self.root, p):
            return ToolResult(ok=False, output="", error="refused: guardrail module is not writable")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"])
            return ToolResult(ok=True, output=f"wrote {args['path']} ({len(args['content'])} bytes)")
        except OSError as e:
            return ToolResult(ok=False, output="", error=f"write failed: {e}")

class EditFileTool(Tool):
    name = "edit_file"; description = "Replace an exact substring in a file within the workdir."
    parameters = {"type": "object",
                  "properties": {"path": PATH_PARAM, "old": {"type": "string"}, "new": {"type": "string"}},
                  "required": ["path", "old", "new"]}
    mutating = True
    def __init__(self, root: Path): self.root = Path(root)
    async def run(self, args: dict) -> ToolResult:
        try:
            p = resolve_in_root(self.root, args["path"])
        except ValueError as e:
            return ToolResult(ok=False, output="", error=str(e))
        if is_guardrail(self.root, p):
            return ToolResult(ok=False, output="", error="refused: guardrail module is not writable")
        try:
            text = p.read_text()
        except OSError as e:
            return ToolResult(ok=False, output="", error=f"read failed: {e}")
        count = text.count(args["old"])
        if count == 0:
            return ToolResult(ok=False, output="", error="old string not found")
        if count > 1:
            return ToolResult(ok=False, output="", error=f"old string is ambiguous ({count} matches)")
        p.write_text(text.replace(args["old"], args["new"]))
        return ToolResult(ok=True, output=f"edited {args['path']}")
