from fastapi.testclient import TestClient
from agent.config import Config
from agent.persistence import Database
from agent.web import create_app

def _app(tmp):
    db=Database(str(tmp/"d.db"))
    def ma(g,o,m): raise AssertionError
    return create_app(Config("http://x/v1","gpt-oss",tmp,[],5,10,"127.0.0.1:8700"), db, ma,
                      prompt_path=tmp/"sp.txt")

def test_get_default_then_save_then_get(tmp_path):
    c=TestClient(_app(tmp_path))
    assert len(c.get("/api/system-prompt").json()["prompt"]) > 0
    assert c.post("/api/system-prompt", json={"prompt":"You are AON, be concise."}).json()["ok"]
    assert c.get("/api/system-prompt").json()["prompt"] == "You are AON, be concise."

def test_empty_prompt_rejected(tmp_path):
    c=TestClient(_app(tmp_path))
    assert "error" in c.post("/api/system-prompt", json={"prompt":"  "}).json()
