from aon.persistence import Database

def test_session_and_messages(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    sid = db.create_session("t", "/ws", "gpt-oss")
    db.add_message(sid, "user", "hi")
    db.add_message(sid, "assistant", "yo")
    msgs = db.get_messages(sid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hi"

def test_actions_audit(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    sid = db.create_session("t", "/ws", "gpt-oss")
    aid = db.add_action(sid, "run_command", '{"command":"ls"}', "approve", '{"command":"ls"}', "a.txt", "", 0)
    acts = db.get_actions(sid)
    assert len(acts) == 1 and acts[0]["tool"] == "run_command" and acts[0]["decision"] == "approve"
    assert acts[0]["exit_code"] == 0
