from fastapi.testclient import TestClient
from agent.config import Config
from agent.persistence import Database
from agent.web import create_app
from agent.memory import MemoryStore
from agent.skills import SkillStore

def _app(tmp):
    db=Database(str(tmp/"d.db"))
    def ma(g,o,m): raise AssertionError
    MemoryStore(tmp/"memory.md").append("fact one")
    SkillStore(tmp/"sk").write("deploy","Deploy steps\nbody")
    return create_app(Config("http://x/v1","gpt-oss",tmp,[],5,10,"127.0.0.1:8700"), db, ma,
                      mem_path=tmp/"memory.md", skills_dir=tmp/"sk")

def test_api_memory(tmp_path):
    c=TestClient(_app(tmp_path))
    eps=c.get("/api/memory").json()["episodes"]
    assert any(e["content"]=="fact one" for e in eps)

def test_api_skills(tmp_path):
    c=TestClient(_app(tmp_path))
    sk=c.get("/api/skills").json()["skills"]
    assert sk and sk[0]["name"]=="deploy" and sk[0]["desc"]=="Deploy steps"
