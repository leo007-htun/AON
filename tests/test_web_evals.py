from fastapi.testclient import TestClient
from agent.config import Config
from agent.persistence import Database
from agent.web import create_app
from agent.skills import SkillStore


def _app(tmp, runner=None):
    db = Database(str(tmp / "d.db"))
    s = SkillStore(tmp / "sk")
    s.write("demo", "---\nname: demo\ndescription: hi\n---\nb")
    def ma(g, o, m): raise AssertionError
    return create_app(Config("http://x/v1", "m", tmp, [], 5, 10, "127.0.0.1:8700"), db, ma,
                      skills_dir=tmp / "sk", eval_runner_factory=runner)


def test_get_skill_detail(tmp_path):
    c = TestClient(_app(tmp_path))
    j = c.get("/api/skills/demo").json()
    assert j["name"] == "demo" and j["description"] == "hi" and "SKILL.md" in j["files"]


def test_report_empty_then_run(tmp_path):
    class FakeRunner:
        def __init__(self, *a, **k): pass
        async def run_suite(self, name, output_runs=1, trigger_runs=3):
            return {"skill": name, "summary": {"delta": {"pass_rate": 0.5}}}
    c = TestClient(_app(tmp_path, runner=lambda cfg, store: FakeRunner()))
    assert c.get("/api/skills/demo/report").json() == {}
    assert c.post("/api/skills/demo/evals/run", json={}).json()["skill"] == "demo"
