import pytest
from pathlib import Path
from aon.tools.files_read import ReadFileTool, ListDirTool
from aon.tools.files_write import WriteFileTool, EditFileTool
from aon.tools.paths import resolve_in_root
from aon.engine import Agent
from aon.llm_client import ChatResult
from aon.tools.base import ToolRegistry
from aon.approval_gate import AutoGate

@pytest.mark.parametrize("cls", [ReadFileTool, ListDirTool, WriteFileTool, EditFileTool])
def test_path_param_tells_model_paths_are_workspace_relative(cls, tmp_path):
    # the model guessed absolute paths like /home/parker when nothing said what "path" means
    d = cls(tmp_path).parameters["properties"]["path"].get("description", "")
    assert "relative" in d and "'.'" in d

def test_outside_path_error_suggests_relative_path(tmp_path):
    with pytest.raises(ValueError) as e:
        resolve_in_root(tmp_path, "/opt/work")
    msg = str(e.value)
    assert "outside the workdir" in msg and "'.'" in msg

@pytest.mark.asyncio
async def test_list_dir_dot_lists_workspace_root(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    r = await ListDirTool(tmp_path).run({"path": "."})
    assert r.ok and "a.txt" in r.output

class EmptyLLM:
    async def chat(self, m, tools=None): return ChatResult("", [])

@pytest.mark.asyncio
async def test_empty_model_reply_is_reported_not_blank():
    res = await Agent(EmptyLLM(), ToolRegistry(), AutoGate()).run("hi")
    assert res.final.strip() and "empty" in res.final.lower()
    assert res.status == "stuck"
