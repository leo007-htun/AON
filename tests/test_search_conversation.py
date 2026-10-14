import pytest
from agent.persistence import Database
from agent.memory_extract import SearchConversationTool


def _db(tmp):
    d = Database(str(tmp / "a.db"))
    for i in range(15):
        d.add_turn("s1", "user", f"message about topic {i}")
    d.add_turn("s1", "user", "we chose postgres for the database")
    d.add_turn("s2", "user", "unrelated other session postgres")
    return d


def test_search_turns_finds_old_scoped(tmp_path):
    d = _db(tmp_path)
    r = d.search_turns("s1", "postgres database")
    assert any("postgres" in t["content"] for t in r)
    assert all("other session" not in t["content"] for t in r)


@pytest.mark.asyncio
async def test_search_conversation_tool(tmp_path):
    d = _db(tmp_path)
    t = SearchConversationTool(d, "s1")
    assert not t.mutating
    out = (await t.run({"query": "postgres"})).output
    assert "postgres" in out
    empty = (await SearchConversationTool(d, "s1").run({"query": "zzzznope"})).output
    assert "no match" in empty.lower()
