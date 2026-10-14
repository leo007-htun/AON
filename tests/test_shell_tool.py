import pytest
from agent.tools.shell import RunCommandTool

@pytest.mark.asyncio
async def test_run_ok(tmp_path):
    res = await RunCommandTool(tmp_path, timeout=10).run({"command": "echo hi"})
    assert res.ok and "hi" in res.output and "exit code: 0" in res.output

@pytest.mark.asyncio
async def test_run_nonzero_is_observation(tmp_path):
    res = await RunCommandTool(tmp_path, timeout=10).run({"command": "ls /nope-xyz"})
    assert res.ok and "exit code:" in res.output and "exit code: 0" not in res.output

@pytest.mark.asyncio
async def test_run_timeout_not_ok(tmp_path):
    res = await RunCommandTool(tmp_path, timeout=1).run({"command": "sleep 30"})
    assert not res.ok and "timed out" in (res.error or "")
