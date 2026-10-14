from pathlib import Path
from aon.tools.base import Tool, ToolResult
from aon.executor import run_command

class RunCommandTool(Tool):
    name = "run_command"
    description = "Run a shell command (bash) in the workdir. Prefer non-interactive flags (-y). sudo is allowed."
    parameters = {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}
    mutating = True
    def __init__(self, root: Path, timeout: int):
        self.root = Path(root); self.timeout = timeout
    async def run(self, args: dict) -> ToolResult:
        r = await run_command(args["command"], cwd=self.root, timeout=self.timeout)
        if r.timed_out:
            return ToolResult(ok=False, output="", error=r.stderr)
        parts = []
        if r.stdout.strip(): parts.append(r.stdout.rstrip())
        if r.stderr.strip(): parts.append("[stderr]\n" + r.stderr.rstrip())
        parts.append(f"exit code: {r.exit_code}")
        return ToolResult(ok=True, output="\n".join(parts))
