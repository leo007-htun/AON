import pytest
from agent.memory import MemoryStore, RememberTool

def test_read_empty(tmp_path):
    assert MemoryStore(tmp_path/"m.md").read() == ""

@pytest.mark.asyncio
async def test_remember_appends(tmp_path):
    s = MemoryStore(tmp_path/"sub"/"m.md")
    t = RememberTool(s)
    assert not t.mutating
    r = await t.run({"content":"tests use pytest -q"})
    assert r.ok and "tests use pytest -q" in s.read()
    await t.run({"content":"second"})
    assert s.read().count("- ") == 2
