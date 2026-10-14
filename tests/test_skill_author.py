import pytest
from aon.skill_author import draft_skill_md, DraftSkillTool
from aon.skills import SkillStore
from aon.llm_client import ChatResult


class FakeLLM:
    def __init__(self, c):
        self.c = c
    async def chat(self, m, tools=None):
        return ChatResult(content=self.c)


@pytest.mark.asyncio
async def test_draft_skill_md_parses_frontmatter():
    md = "---\nname: mem-layers\ndescription: memory layering. Use when designing agent memory.\n---\nbody steps"
    name, out = await draft_skill_md("content", "http://x/doc", FakeLLM(md))
    assert name == "mem-layers" and "body steps" in out and out.startswith("---")


@pytest.mark.asyncio
async def test_draft_skill_md_wraps_when_no_frontmatter():
    name, out = await draft_skill_md("c", "http://x/short-term-vs-long-term", FakeLLM("just prose no frontmatter"))
    assert out.startswith("---") and "name:" in out and name == "short-term-vs-long-term"
    assert "just prose no frontmatter" in out


@pytest.mark.asyncio
async def test_draft_skill_tool_writes_skill(tmp_path):
    s = SkillStore(tmp_path)
    async def fetch(url):
        return "Step 1 do x. Step 2 do y."
    md = "---\nname: flow\ndescription: a flow. Use for flows.\n---\nsteps here"
    r = await DraftSkillTool(fetch, FakeLLM(md), s).run({"url": "http://x/flow"})
    assert r.ok and "flow" in r.output
    assert "steps here" in s.read("flow") and "SKILL.md" in s.files("flow")


@pytest.mark.asyncio
async def test_draft_skill_tool_strips_fences(tmp_path):
    s = SkillStore(tmp_path)
    async def fetch(url):
        return "content"
    fenced = "```markdown\n---\nname: fenced\ndescription: d. use it.\n---\nbody text\n```"
    r = await DraftSkillTool(fetch, FakeLLM(fenced), s).run({"url": "http://x", "name": "fenced"})
    assert r.ok and "body text" in s.read("fenced") and "```" not in s.read("fenced")


@pytest.mark.asyncio
async def test_draft_skill_tool_empty_page_errors(tmp_path):
    s = SkillStore(tmp_path)
    async def fetch(url):
        return ""
    r = await DraftSkillTool(fetch, FakeLLM("x"), s).run({"url": "http://x"})
    assert not r.ok
