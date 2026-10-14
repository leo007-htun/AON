import pytest
from aon.memory import EpisodicMemory, RememberTool


def _m(tmp):
    return EpisodicMemory(tmp / "ep.db")


def test_add_and_dedup(tmp_path):
    m = _m(tmp_path)
    a = m.add("make turbo is my build cmd")
    b = m.add("make turbo is my build cmd")
    assert a == b
    assert len([e for e in m.all() if e["active"]]) == 1


def test_supersede_marks_old_inactive(tmp_path):
    m = _m(tmp_path)
    old = m.add("prefers brief responses", type="preference")
    new = m.add("prefers detailed responses", type="preference", supersedes=old)
    act = [e["id"] for e in m.recent(10)]
    assert new in act and old not in act
    assert m.get(old)["active"] == 0


def test_recent_is_bounded_and_newest_first(tmp_path):
    m = _m(tmp_path)
    for i in range(20):
        m.add(f"fact number {i}")
    r = m.recent(8)
    assert len(r) == 8 and r[0]["content"] == "fact number 19"


def test_recent_excludes_stale_when_newer_exist(tmp_path):
    m = _m(tmp_path)
    m.add("make turbo")
    for i in range(8):
        m.add(f"newer {i}")
    txt = m.as_prompt_text(8)
    assert "make turbo" not in txt and "newer 7" in txt


def test_search_keyword_rank(tmp_path):
    m = _m(tmp_path)
    m.add("the database is postgres", tags="db")
    m.add("the weather is sunny")
    r = m.search("database postgres")
    assert r and r[0]["content"] == "the database is postgres"


def test_delete_and_mark_outdated(tmp_path):
    m = _m(tmp_path)
    i = m.add("temp")
    m.mark_outdated(i)
    assert m.get(i)["active"] == 0
    m.delete(i)
    assert m.get(i) is None


@pytest.mark.asyncio
async def test_remember_tool_and_shim(tmp_path):
    m = _m(tmp_path)
    t = RememberTool(m)
    assert not t.mutating
    await t.run({"content": "likes tuesday meetings", "type": "preference"})
    assert "tuesday" in m.read()
    m.append("legacy fact")
    assert "legacy fact" in m.read()
