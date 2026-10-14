import base64
import io
import zipfile
from fastapi.testclient import TestClient
from agent.config import Config
from agent.persistence import Database
from agent.web import create_app
from agent.skills import SkillStore

SK = "---\nname: {n}\ndescription: does {n}\n---\n\nbody\n"


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p, c in files.items():
            z.writestr(p, c)
    return buf.getvalue()


def _client(tmp):
    db = Database(str(tmp / "d.db"))
    def ma(g, o, m): raise AssertionError
    app = create_app(Config("http://x/v1", "gpt-oss", tmp, [], 5, 10, "127.0.0.1:8700"), db, ma,
                     mem_path=tmp / "m.db", skills_dir=tmp / "sk")
    return TestClient(app)


def test_import_zip_preview_then_install(tmp_path):
    c = _client(tmp_path)
    b64 = base64.b64encode(_zip({"r/skills/a/SKILL.md": SK.format(n="a"),
                                 "r/skills/a/scripts/x.py": "print(1)",
                                 "r/skills/b/SKILL.md": SK.format(n="b")})).decode()
    prev = c.post("/api/skills/import", json={"zip_b64": b64, "dry_run": True}).json()
    assert [s["name"] for s in prev["skills"]] == ["a", "b"]
    assert prev["skills"][0]["scripts"] == ["scripts/x.py"]
    assert prev["skills"][0]["exists"] is False
    assert SkillStore(tmp_path / "sk").list() == []          # dry run writes nothing
    res = c.post("/api/skills/import", json={"zip_b64": b64, "names": ["a"]}).json()
    assert res["installed"] == ["a"]
    assert [s["name"] for s in c.get("/api/skills").json()["skills"]] == ["a"]
    prev2 = c.post("/api/skills/import", json={"zip_b64": b64, "dry_run": True}).json()
    assert prev2["skills"][0]["exists"] is True


def test_import_paste_skill_md(tmp_path):
    c = _client(tmp_path)
    res = c.post("/api/skills/import", json={"skill_md": SK.format(n="pasted")}).json()
    assert res["installed"] == ["pasted"]


def test_import_url_uses_fetcher(tmp_path, monkeypatch):
    import agent.skill_import as si
    seen = {}
    async def fake_fetch(url):
        seen["url"] = url
        return _zip({"o-r-abc/skills/g/SKILL.md": SK.format(n="g")})
    monkeypatch.setattr(si, "fetch_github_zip", fake_fetch)
    c = _client(tmp_path)
    res = c.post("/api/skills/import", json={"url": "https://github.com/o/r"}).json()
    assert seen["url"] == "https://github.com/o/r"
    assert res["installed"] == ["g"]


def test_import_errors_are_reported(tmp_path):
    c = _client(tmp_path)
    assert "error" in c.post("/api/skills/import", json={"zip_b64": "!!notb64"}).json()
    assert "error" in c.post("/api/skills/import", json={}).json()
    bad = base64.b64encode(_zip({"x/../../e": "p", "x/SKILL.md": SK.format(n="x")})).decode()
    assert "error" in c.post("/api/skills/import", json={"zip_b64": bad}).json()


def test_import_paste_existing_reports_skip_then_overwrite(tmp_path):
    c = _client(tmp_path)
    md = SK.format(n="dup")
    assert c.post("/api/skills/import", json={"skill_md": md}).json()["installed"] == ["dup"]
    again = c.post("/api/skills/import", json={"skill_md": md}).json()
    assert again["installed"] == [] and again["skipped"][0]["reason"] == "already installed"
    assert c.post("/api/skills/import", json={"skill_md": md, "overwrite": True}).json()["installed"] == ["dup"]
