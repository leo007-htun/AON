from fastapi.testclient import TestClient
from aon.config import Config
from aon.persistence import Database
from aon.web import create_app
from aon.memory import EpisodicMemory


def _app(tmp):
    db = Database(str(tmp / "d.db"))
    m = EpisodicMemory(tmp / "ep.db")
    m.add("fact one", tags="x")
    m.add("fact two")
    def ma(g, o, m_): raise AssertionError
    return create_app(Config("http://x/v1", "m", tmp, [], 5, 10, "127.0.0.1:8700"), db, ma,
                      mem_path=tmp / "ep.db")


def test_list_and_delete_and_outdated(tmp_path):
    c = TestClient(_app(tmp_path))
    eps = c.get("/api/memory").json()["episodes"]
    assert any(e["content"] == "fact one" for e in eps)
    fid = [e for e in eps if e["content"] == "fact one"][0]["id"]
    assert c.post(f"/api/memory/{fid}/outdated").json()["ok"]
    assert all(e["id"] != fid for e in c.get("/api/memory").json()["episodes"])
    assert c.delete("/api/memory").json()["ok"]
    assert c.get("/api/memory").json()["episodes"] == []
