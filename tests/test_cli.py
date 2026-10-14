"""The `aon` command: init makes a working home folder; serve and mcp run from it."""
import os
import pytest
from aon import __version__
from aon.cli import main, resolve_home, init_home
from aon.config import load_config


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0 and __version__ in capsys.readouterr().out


def test_home_comes_from_flag_then_env_then_default(monkeypatch, tmp_path):
    monkeypatch.delenv("AON_HOME", raising=False)
    assert resolve_home(str(tmp_path / "a")) == (tmp_path / "a").resolve()
    monkeypatch.setenv("AON_HOME", str(tmp_path / "b"))
    assert resolve_home(None) == (tmp_path / "b").resolve()
    monkeypatch.delenv("AON_HOME")
    assert resolve_home(None).name == ".aon"


def test_init_makes_a_loadable_home(tmp_path):
    home = tmp_path / "h"
    created = init_home(home, ollama="http://gpu:11434/v1", model="qwen3:14b", bind="127.0.0.1:9000")
    assert (home / "config.toml").exists() and "config.toml" in created
    for d in ("workspace", "memory", "skills"):
        assert (home / d).is_dir()
    assert (home / "mcp.json").read_text().strip() == "[]"
    assert (home / "skills" / "creating-skills" / "SKILL.md").exists()     # the built-in skill
    cfg = load_config(home / "config.toml")
    assert cfg.ollama_base_url == "http://gpu:11434/v1" and cfg.default_model == "qwen3:14b"
    assert cfg.workdir == (home / "workspace") and cfg.bind == "127.0.0.1:9000"


def test_init_never_overwrites_without_force(tmp_path):
    home = tmp_path / "h"
    init_home(home, ollama="http://a/v1", model="m1", bind="127.0.0.1:1")
    (home / "config.toml").write_text((home / "config.toml").read_text().replace("m1", "mine"))
    assert "config.toml" not in init_home(home, ollama="http://b/v1", model="m2", bind="127.0.0.1:2")
    assert 'default_model = "mine"' in (home / "config.toml").read_text()
    init_home(home, ollama="http://b/v1", model="m2", bind="127.0.0.1:2", force=True)
    assert 'default_model = "m2"' in (home / "config.toml").read_text()


def test_init_command(tmp_path, capsys):
    assert main(["init", "--home", str(tmp_path / "h"), "--model", "llama3.1"]) == 0
    out = capsys.readouterr().out
    assert "aon serve" in out and str(tmp_path / "h") in out
    assert load_config(tmp_path / "h" / "config.toml").default_model == "llama3.1"


@pytest.mark.parametrize("cmd", ["serve", "mcp"])
def test_commands_explain_a_missing_config(tmp_path, capsys, cmd):
    assert main([cmd, "--home", str(tmp_path / "empty")]) == 2
    assert "aon init" in capsys.readouterr().err


def test_serve_builds_the_app_inside_home(tmp_path, monkeypatch):
    home = tmp_path / "h"
    init_home(home, ollama="http://x/v1", model="m", bind="127.0.0.1:8123")
    seen = {}
    import uvicorn
    def fake_run(app, host, port, **kw):
        seen.update(app=app, host=host, port=port, cwd=os.getcwd())
    monkeypatch.setattr(uvicorn, "run", fake_run)
    cwd = os.getcwd()
    try:
        assert main(["serve", "--home", str(home), "--port", "9999"]) == 0
    finally:
        os.chdir(cwd)
    assert seen["host"] == "127.0.0.1" and seen["port"] == 9999 and seen["cwd"] == str(home.resolve())
    assert (home / "workspace" / "app.db").exists()


def test_existing_app_folder_works_as_home(tmp_path, monkeypatch):
    """A production folder (config.toml, workspace/, memory/, skills/) is a valid home as is."""
    home = tmp_path / "app"
    (home / "workspace").mkdir(parents=True)
    (home / "config.toml").write_text(
        f'ollama_base_url = "http://x/v1"\ndefault_model = "m"\nworkdir = "{(home / "workspace").as_posix()}"\n'
        'bind = "192.168.1.50:8700"\n')
    import uvicorn
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port, **kw: seen.update(host=host, port=port))
    cwd = os.getcwd()
    try:
        assert main(["serve", "--home", str(home)]) == 0
    finally:
        os.chdir(cwd)
    assert seen == {"host": "192.168.1.50", "port": 8700}
