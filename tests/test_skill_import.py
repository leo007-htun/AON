import io
import stat
import zipfile
import pytest
from aon.skills import SkillStore
from aon.skill_import import (scan_zip, install_zip, zip_from_skill_md, github_zip_url,
                                ImportError_)


def _zip(files: dict, symlinks: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p, c in files.items():
            z.writestr(p, c)
        for p, target in (symlinks or {}).items():
            zi = zipfile.ZipInfo(p)
            zi.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(zi, target)
    return buf.getvalue()


SK = "---\nname: {n}\ndescription: does {n}\n---\n\nbody\n"


def test_scan_finds_skills_in_repo_layout():
    data = _zip({"repo-main/README.md": "x",
                 "repo-main/skills/alpha/SKILL.md": SK.format(n="alpha"),
                 "repo-main/skills/alpha/scripts/run.sh": "echo hi",
                 "repo-main/skills/beta/SKILL.md": SK.format(n="beta"),
                 "repo-main/skills/beta/references/r.md": "ref"})
    found = {s["name"]: s for s in scan_zip(data)}
    assert set(found) == {"alpha", "beta"}
    assert found["alpha"]["description"] == "does alpha"
    assert found["alpha"]["files"] == ["SKILL.md", "scripts/run.sh"]
    assert found["alpha"]["scripts"] == ["scripts/run.sh"]
    assert found["beta"]["scripts"] == []


def test_scan_ignores_nested_skill_inside_skill():
    data = _zip({"a/SKILL.md": SK.format(n="a"), "a/examples/b/SKILL.md": SK.format(n="b")})
    assert [s["name"] for s in scan_zip(data)] == ["a"]


def test_scan_flags_missing_description():
    data = _zip({"x/SKILL.md": "---\nname: x\n---\nbody"})
    [s] = scan_zip(data)
    assert s["error"] and "description" in s["error"]


def test_scan_flags_long_description():
    data = _zip({"x/SKILL.md": "---\nname: x\ndescription: " + "d" * 1100 + "\n---\nbody"})
    [s] = scan_zip(data)
    assert "1024" in s["error"]


def test_scan_rejects_zip_slip():
    data = _zip({"x/SKILL.md": SK.format(n="x"), "x/../../evil.txt": "pwn"})
    with pytest.raises(ImportError_):
        scan_zip(data)


def test_scan_rejects_absolute_path():
    data = _zip({"x/SKILL.md": SK.format(n="x"), "/etc/evil": "pwn"})
    with pytest.raises(ImportError_):
        scan_zip(data)


def test_scan_rejects_symlink():
    data = _zip({"x/SKILL.md": SK.format(n="x")}, symlinks={"x/link": "/etc/passwd"})
    with pytest.raises(ImportError_):
        scan_zip(data)


def test_scan_rejects_not_a_zip():
    with pytest.raises(ImportError_):
        scan_zip(b"not a zip")


def test_scan_rejects_oversize(monkeypatch):
    import aon.skill_import as si
    monkeypatch.setattr(si, "MAX_TOTAL_BYTES", 10)
    with pytest.raises(ImportError_):
        scan_zip(_zip({"x/SKILL.md": SK.format(n="x")}))


def test_install_writes_all_files(tmp_path):
    store = SkillStore(tmp_path / "sk")
    data = _zip({"r/skills/alpha/SKILL.md": SK.format(n="alpha"),
                 "r/skills/alpha/scripts/run.sh": "echo hi"})
    res = install_zip(store, data)
    assert res["installed"] == ["alpha"]
    assert store.read("alpha", "scripts/run.sh") == "echo hi"
    assert ("alpha", "does alpha") in store.list()


def test_install_only_selected(tmp_path):
    store = SkillStore(tmp_path / "sk")
    data = _zip({"a/SKILL.md": SK.format(n="a"), "b/SKILL.md": SK.format(n="b")})
    res = install_zip(store, data, names=["b"])
    assert res["installed"] == ["b"]
    assert [n for n, _ in store.list()] == ["b"]


def test_install_skips_existing_unless_overwrite(tmp_path):
    store = SkillStore(tmp_path / "sk")
    store.write("a", SK.format(n="a"))
    store.write("a", "old", "stale.txt")
    data = _zip({"a/SKILL.md": SK.format(n="a").replace("body", "new")})
    res = install_zip(store, data)
    assert res["installed"] == [] and res["skipped"][0]["name"] == "a"
    res = install_zip(store, data, overwrite=True)
    assert res["installed"] == ["a"]
    assert "new" in store.read("a")
    assert store.files("a") == ["SKILL.md"]   # overwrite replaces the folder


def test_install_skips_invalid(tmp_path):
    store = SkillStore(tmp_path / "sk")
    data = _zip({"x/SKILL.md": "---\nname: x\n---\nbody"})
    res = install_zip(store, data)
    assert res["installed"] == [] and "description" in res["skipped"][0]["reason"]


def test_install_uses_folder_name_when_frontmatter_name_invalid(tmp_path):
    store = SkillStore(tmp_path / "sk")
    data = _zip({"good-name/SKILL.md": "---\nname: ../bad\ndescription: d\n---\nb"})
    assert install_zip(store, data)["installed"] == ["good-name"]


def test_zip_from_skill_md_roundtrip(tmp_path):
    store = SkillStore(tmp_path / "sk")
    data = zip_from_skill_md(SK.format(n="pasted"))
    assert install_zip(store, data)["installed"] == ["pasted"]


def test_zip_from_skill_md_requires_some_name():
    with pytest.raises(ImportError_):
        zip_from_skill_md("---\ndescription: d\n---\n")


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/obra/superpowers", "https://api.github.com/repos/obra/superpowers/zipball"),
    ("https://github.com/obra/superpowers.git", "https://api.github.com/repos/obra/superpowers/zipball"),
    ("github.com/obra/superpowers/", "https://api.github.com/repos/obra/superpowers/zipball"),
    ("https://github.com/anthropics/skills/tree/main/skills",
     "https://api.github.com/repos/anthropics/skills/zipball/main"),
])
def test_github_zip_url(url, expected):
    assert github_zip_url(url) == expected


def test_github_zip_url_rejects_other_hosts():
    with pytest.raises(ImportError_):
        github_zip_url("https://evil.example.com/a/b")


def test_zip_from_skill_md_without_frontmatter_uses_heading(tmp_path):
    store = SkillStore(tmp_path / "sk")
    md = "# Deploy Helper\n\nUse when deploying the app to production.\n\n1. build\n2. ship\n"
    assert install_zip(store, zip_from_skill_md(md))["installed"] == ["deploy-helper"]
    assert ("deploy-helper", "Use when deploying the app to production.") in store.list()
    assert "1. build" in store.read("deploy-helper")


def test_zip_from_skill_md_partial_frontmatter(tmp_path):
    store = SkillStore(tmp_path / "sk")
    md = "---\ndescription: Summarise logs\n---\n# Log Summary\nbody\n"
    assert install_zip(store, zip_from_skill_md(md))["installed"] == ["log-summary"]
    assert store.read("log-summary").startswith("---\nname: log-summary\ndescription: Summarise logs\n---")


def test_zip_from_skill_md_plain_title_line(tmp_path):
    # text copied from a rendered page: no frontmatter, no '#'
    store = SkillStore(tmp_path / "sk")
    md = ("Coding Agent\nUse for background feature builds and PR reviews.\nHard rules\n"
          "Always launch with background:true.\n")
    assert install_zip(store, zip_from_skill_md(md))["installed"] == ["coding-agent"]
    assert ("coding-agent", "Use for background feature builds and PR reviews.") in store.list()
    assert "Hard rules" in store.read("coding-agent")


def test_zip_from_skill_md_long_first_line_is_not_a_title():
    with pytest.raises(ImportError_):
        zip_from_skill_md("This is a long first sentence that is clearly body text and not a title at all.\nmore")
