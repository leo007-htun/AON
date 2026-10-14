import pytest
from aon.skills import SkillStore, WriteSkillTool
from aon.composite_skills import CompositeSkills
from aon.mcp_client import McpPromptSpec


class FakeMgr:
    def prompts(self): return [McpPromptSpec("pw", "howto", "how to browse")]
    async def get_prompt(self, server, name, args=None): return "PROMPT BODY"


@pytest.mark.asyncio
async def test_list_merges_file_and_mcp(tmp_path):
    s = SkillStore(tmp_path)
    await WriteSkillTool(s).run({"name": "deploy", "content": "---\nname: deploy\ndescription: ship\n---\nx"})
    cs = CompositeSkills(s, FakeMgr())
    names = dict(cs.list())
    assert "deploy" in names and "mcp:pw:howto" in names
    items = {i["name"]: i["source"] for i in cs.items()}
    assert items["deploy"] == "file" and items["mcp:pw:howto"] == "mcp"


@pytest.mark.asyncio
async def test_read_file_and_mcp(tmp_path):
    s = SkillStore(tmp_path)
    await WriteSkillTool(s).run({"name": "deploy", "content": "body here"})
    cs = CompositeSkills(s, FakeMgr())
    assert "body here" in cs.read("deploy")
    assert "PROMPT BODY" in (await cs.aread("mcp:pw:howto"))
