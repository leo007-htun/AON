import httpx, pytest
from pathlib import Path
from fastapi.testclient import TestClient
from aon.config import Config
from aon.persistence import Database
from aon.web import create_app

def _cfg(tmp):
    return Config("http://x/v1","gpt-oss",tmp,[],5,10,"127.0.0.1:8700")

def _app(tmp, models=None, fail=False):
    db = Database(str(tmp/"d.db"))
    def make_agent(gate, on_event, model): raise AssertionError("not used in REST test")
    app = create_app(_cfg(tmp), db, make_agent)
    import aon.web as W
    async def fake_list(base, model):
        if fail: raise RuntimeError("down")
        return models or ["gpt-oss"]
    W._list_models = fake_list
    return app

def test_models_ok(tmp_path):
    c = TestClient(_app(tmp_path, models=["gpt-oss","coder"]))
    r = c.get("/api/models")
    assert r.status_code == 200 and r.json()["models"] == ["gpt-oss","coder"]

def test_models_error_not_500(tmp_path):
    c = TestClient(_app(tmp_path, fail=True))
    r = c.get("/api/models")
    assert r.status_code == 200 and "error" in r.json()
