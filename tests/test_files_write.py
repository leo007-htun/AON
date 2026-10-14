import pytest
from agent.tools.files_write import WriteFileTool, EditFileTool

@pytest.mark.asyncio
async def test_write_creates_file(tmp_path):
    res = await WriteFileTool(tmp_path).run({"path": "out.txt", "content": "hi"})
    assert res.ok and (tmp_path / "out.txt").read_text() == "hi"

@pytest.mark.asyncio
async def test_write_blocks_guardrail_path(tmp_path):
    (tmp_path / "src/agent").mkdir(parents=True)
    res = await WriteFileTool(tmp_path).run({"path": "src/agent/executor.py", "content": "x"})
    assert not res.ok and "guardrail" in (res.error or "").lower()
    assert not (tmp_path / "src/agent/executor.py").exists()

@pytest.mark.asyncio
async def test_write_blocks_escape(tmp_path):
    res = await WriteFileTool(tmp_path).run({"path": "../x", "content": "x"})
    assert not res.ok

@pytest.mark.asyncio
async def test_edit_replaces_exact(tmp_path):
    (tmp_path / "f.py").write_text("a = 1\nb = 2\n")
    res = await EditFileTool(tmp_path).run({"path": "f.py", "old": "b = 2", "new": "b = 3"})
    assert res.ok and (tmp_path / "f.py").read_text() == "a = 1\nb = 3\n"

@pytest.mark.asyncio
async def test_edit_missing_old_errors(tmp_path):
    (tmp_path / "f.py").write_text("a = 1\n")
    res = await EditFileTool(tmp_path).run({"path": "f.py", "old": "zzz", "new": "q"})
    assert not res.ok and "not found" in (res.error or "").lower()
