from agent.persistence import Database


def _db(tmp): return Database(str(tmp / "a.db"))


def test_add_and_recent_oldest_first(tmp_path):
    db = _db(tmp_path)
    db.add_turn("s1", "user", "make a skill from a url")
    db.add_turn("s1", "assistant", "Which URL?")
    turns = db.recent_turns("s1")
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["content"] == "make a skill from a url"


def test_recent_turns_capped(tmp_path):
    db = _db(tmp_path)
    for i in range(20):
        db.add_turn("s1", "user", f"m{i}")
    turns = db.recent_turns("s1", limit=5)
    assert len(turns) == 5 and turns[-1]["content"] == "m19"


def test_isolation_between_convos(tmp_path):
    db = _db(tmp_path)
    db.add_turn("s1", "user", "a")
    db.add_turn("s2", "user", "b")
    assert [t["content"] for t in db.recent_turns("s1")] == ["a"]
    assert [t["content"] for t in db.recent_turns("s2")] == ["b"]


def test_clear_and_unknown(tmp_path):
    db = _db(tmp_path)
    db.add_turn("s1", "user", "a")
    db.clear_conversation("s1")
    assert db.recent_turns("s1") == [] and db.recent_turns("nope") == []
