# GUARDRAIL MODULE -- not editable by agent tools (see config.GUARDRAIL_PATHS).
import asyncio, os, signal
from dataclasses import dataclass
from pathlib import Path

@dataclass
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool

def _truncate(b: bytes, limit: int) -> str:
    s = b.decode(errors="replace")
    if len(s) <= limit:
        return s
    half = limit // 2
    return s[:half] + "\n...[truncated]...\n" + s[-half:]

async def run_command(command: str, cwd: Path, timeout: int, max_output: int = 100_000) -> CommandResult:
    proc = await asyncio.create_subprocess_exec(
        "bash", "-lc", command,
        cwd=str(cwd),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return CommandResult(_truncate(out, max_output), _truncate(err, max_output), proc.returncode, False)
    except asyncio.TimeoutError:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()
        return CommandResult("", f"timed out after {timeout}s", -1, True)
