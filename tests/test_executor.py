import pytest
from pathlib import Path
from aon.executor import run_command, CommandResult

@pytest.mark.asyncio
async def test_runs_and_captures(tmp_path):
    r = await run_command("echo hello", cwd=tmp_path, timeout=10)
    assert isinstance(r, CommandResult)
    assert r.exit_code == 0 and r.stdout.strip() == "hello" and not r.timed_out

@pytest.mark.asyncio
async def test_nonzero_exit(tmp_path):
    r = await run_command("exit 3", cwd=tmp_path, timeout=10)
    assert r.exit_code == 3 and not r.timed_out

@pytest.mark.asyncio
async def test_timeout_kills(tmp_path):
    r = await run_command("sleep 30", cwd=tmp_path, timeout=1)
    assert r.timed_out and r.exit_code != 0

@pytest.mark.asyncio
async def test_output_truncated(tmp_path):
    r = await run_command("yes abc | head -c 500000", cwd=tmp_path, timeout=10, max_output=1000)
    assert len(r.stdout) <= 1100 and "truncated" in r.stdout
