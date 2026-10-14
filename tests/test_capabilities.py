import pytest
from aon.skills import SkillStore, WriteSkillTool
from aon.capabilities import CapabilitiesTool
from aon.tools.base import ToolRegistry


@pytest.mark.asyncio
async def test_skill_info_parses_requires(tmp_path):
    s = SkillStore(tmp_path)
    await WriteSkillTool(s).run({"name": "pw",
        "content": "---\nname: pw\ndescription: run playwright\nrequires: node npx\n---\nsteps"})
    info = s.info("pw")
    assert info["requires"] == "node npx" and info["description"] == "run playwright"


@pytest.mark.asyncio
async def test_capabilities_lists_tools_and_skills(tmp_path):
    s = SkillStore(tmp_path)
    await WriteSkillTool(s).run({"name": "pw",
        "content": "---\nname: pw\ndescription: run playwright\nrequires: node\n---\nx"})
    reg = ToolRegistry()
    cap = CapabilitiesTool(reg, s)
    reg.register(cap)
    out = (await cap.run({})).output
    assert "capabilities" in out.lower() and "pw" in out and "node" in out and not cap.mutating
