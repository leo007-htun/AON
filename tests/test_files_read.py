import pytest
from pathlib import Path
from agent.tools.paths import resolve_in_root
from agent.tools.files_read import ReadFileTool, ListDirTool

def test_resolve_rejects_escape(tmp_path):
    with pytest.raises(ValueError):
        resolve_in_root(tmp_path, "../outside.txt")
    with pytest.raises(ValueError):
        resolve_in_root(tmp_path, "/etc/passwd")

@pytest.mark.asyncio
async def test_read_file_ok(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    res = await ReadFileTool(tmp_path).run({"path": "a.txt"})
    assert res.ok and res.output == "hello"

@pytest.mark.asyncio
async def test_read_file_escape_blocked(tmp_path):
    res = await ReadFileTool(tmp_path).run({"path": "../x"})
    assert not res.ok and "outside" in (res.error or "").lower()

@pytest.mark.asyncio
async def test_list_dir(tmp_path):
    (tmp_path / "a.txt").write_text("x"); (tmp_path / "sub").mkdir()
    res = await ListDirTool(tmp_path).run({"path": "."})
    assert "a.txt" in res.output and "sub/" in res.output
