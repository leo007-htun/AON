import pytest
from agent.skills import SkillStore, ReadSkillTool, WriteSkillTool

def test_safe_rejects_escape(tmp_path):
    s = SkillStore(tmp_path)
    for bad in ["../x","a/b","/etc/x"]:
        with pytest.raises(ValueError): s._safe(bad)

@pytest.mark.asyncio
async def test_write_then_read_and_list(tmp_path):
    s = SkillStore(tmp_path)
    w = WriteSkillTool(s); r = ReadSkillTool(s)
    assert not w.mutating and not r.mutating
    await w.run({"name":"deploy","content":"Deploy steps\n1. build\n2. ship"})
    assert "Deploy steps" in (await r.run({"name":"deploy"})).output
    assert s.list() == [("deploy","Deploy steps")]

@pytest.mark.asyncio
async def test_read_missing_is_error(tmp_path):
    res = await ReadSkillTool(SkillStore(tmp_path)).run({"name":"nope"})
    assert not res.ok

@pytest.mark.asyncio
async def test_write_stores_skill_md_folder(tmp_path):
    s = SkillStore(tmp_path)
    await WriteSkillTool(s).run({"name":"deploy","content":"Deploy steps\nbody"})
    f = tmp_path / "deploy" / "SKILL.md"
    assert f.exists()
    assert f.read_text().startswith("---")

@pytest.mark.asyncio
async def test_description_from_frontmatter(tmp_path):
    s = SkillStore(tmp_path)
    content = "---\nname: deploy\ndescription: Ship the app to prod\n---\n\nFirst body line\nmore"
    await WriteSkillTool(s).run({"name":"deploy","content":content})
    assert s.list() == [("deploy", "Ship the app to prod")]
    assert "First body line" in s.read("deploy")


# --- Task 1: SkillStore v2 (multi-file, path-confined, 1024 cap) ---
from agent.skills import SkillFilesTool

@pytest.mark.asyncio
async def test_multifile_write_read_and_files(tmp_path):
    s = SkillStore(tmp_path)
    await WriteSkillTool(s).run({"name":"demo","content":"---\nname: demo\ndescription: d\n---\nbody"})
    await WriteSkillTool(s).run({"name":"demo","content":"ref body","path":"references/a.md"})
    assert (await ReadSkillTool(s).run({"name":"demo","path":"references/a.md"})).output == "ref body"
    files = s.files("demo")
    assert "SKILL.md" in files and "references/a.md" in files

@pytest.mark.asyncio
async def test_path_escape_rejected(tmp_path):
    s = SkillStore(tmp_path)
    r = await WriteSkillTool(s).run({"name":"demo","content":"x","path":"../../etc/x"})
    assert not r.ok

@pytest.mark.asyncio
async def test_description_over_1024_rejected(tmp_path):
    s = SkillStore(tmp_path)
    big = "---\nname: demo\ndescription: " + ("z"*1100) + "\n---\nbody"
    r = await WriteSkillTool(s).run({"name":"demo","content":big})
    assert not r.ok and "1024" in (r.error or "")

@pytest.mark.asyncio
async def test_skill_files_tool_lists(tmp_path):
    s = SkillStore(tmp_path)
    await WriteSkillTool(s).run({"name":"demo","content":"body"})
    out = (await SkillFilesTool(s).run({"name":"demo"})).output
    assert "SKILL.md" in out


def test_parse_frontmatter_strips_quotes_and_joins_block_scalars():
    from agent.skills import parse_frontmatter
    meta, body = parse_frontmatter('---\nname: a\ndescription: "Use when: x"\n---\nbody')
    assert meta["description"] == "Use when: x" and body == "body"
    meta, _ = parse_frontmatter("---\nname: 'b'\ndescription: >\n  line one\n  line two\nlicense: MIT\n---\n")
    assert meta == {"name": "b", "description": "line one line two", "license": "MIT"}
