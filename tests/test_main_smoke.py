from pathlib import Path
from fastapi.testclient import TestClient
from agent.main import build_app_from_config

def test_app_builds_and_serves_index(tmp_path):
    cfg = tmp_path/"config.toml"
    cfg.write_text(
        'ollama_base_url="http://x/v1"\ndefault_model="gpt-oss"\n'
        f'workdir="{tmp_path/"ws"}"\nalways_ask=[]\nmax_steps=5\n'
        'command_timeout=10\nbind="127.0.0.1:8700"\n'
        'telegram_bot_token=""\ntelegram_allowed_chat_ids=[]\n')
    (tmp_path/"ws").mkdir()
    app = build_app_from_config(str(cfg))
    c = TestClient(app)
    assert c.get("/").status_code == 200

def test_index_is_aon_branded(tmp_path):
    cfg = tmp_path/"config.toml"
    cfg.write_text(
        'ollama_base_url="http://x/v1"\ndefault_model="gpt-oss"\n'
        f'workdir="{tmp_path/"ws"}"\nalways_ask=[]\nmax_steps=5\n'
        'command_timeout=10\nbind="127.0.0.1:8700"\n'
        'telegram_bot_token=""\ntelegram_allowed_chat_ids=[]\n')
    (tmp_path/"ws").mkdir()
    from fastapi.testclient import TestClient
    from agent.main import build_app_from_config
    html = TestClient(build_app_from_config(str(cfg))).get("/").text
    assert "AON" in html and "system-prompt" in html

def test_ui_has_odysseus_style_shell(tmp_path):
    cfg = tmp_path/"config.toml"
    cfg.write_text(
        'ollama_base_url="http://x/v1"\ndefault_model="gpt-oss"\n'
        f'workdir="{tmp_path/"ws"}"\nalways_ask=[]\nmax_steps=5\n'
        'command_timeout=10\nbind="127.0.0.1:8700"\n'
        'telegram_bot_token=""\ntelegram_allowed_chat_ids=[]\n')
    (tmp_path/"ws").mkdir()
    from fastapi.testclient import TestClient
    from agent.main import build_app_from_config
    html = TestClient(build_app_from_config(str(cfg))).get("/").text
    for marker in ['AON','/static/lib/cybercore.min.css','id="rail"','data-view="memory"','data-view="skills"','data-view="settings"','system-prompt']:
        assert marker in html, marker

def test_index_uses_aon_logo(tmp_path):
    cfg = tmp_path/"config.toml"
    cfg.write_text(
        'ollama_base_url="http://x/v1"\ndefault_model="gpt-oss"\n'
        f'workdir="{tmp_path/"ws"}"\nalways_ask=[]\nmax_steps=5\n'
        'command_timeout=10\nbind="127.0.0.1:8700"\n'
        'telegram_bot_token=""\ntelegram_allowed_chat_ids=[]\n')
    (tmp_path/"ws").mkdir()
    from fastapi.testclient import TestClient
    from agent.main import build_app_from_config
    c = TestClient(build_app_from_config(str(cfg)))
    html = c.get("/").text
    assert 'class="brand-logo"' in html and 'aria-label="AON logo"' in html
    assert c.get("/static/aon-logo.svg").status_code == 200


def test_index_has_evals_ui(tmp_path):
    cfg = tmp_path/"config.toml"
    cfg.write_text('ollama_base_url="http://x/v1"\ndefault_model="gpt-oss"\n'
        f'workdir="{tmp_path/"ws"}"\nalways_ask=[]\nmax_steps=5\ncommand_timeout=10\n'
        'bind="127.0.0.1:8700"\ntelegram_bot_token=""\ntelegram_allowed_chat_ids=[]\n')
    (tmp_path/"ws").mkdir()
    from fastapi.testclient import TestClient
    from agent.main import build_app_from_config
    html = TestClient(build_app_from_config(str(cfg))).get("/").text
    assert "Run evals" in html and "evals/run" in html


def test_index_has_memory_curation(tmp_path):
    cfg = tmp_path/"config.toml"
    cfg.write_text('ollama_base_url="http://x/v1"\ndefault_model="gpt-oss"\n'
        f'workdir="{tmp_path/"ws"}"\nalways_ask=[]\nmax_steps=5\ncommand_timeout=10\n'
        'bind="127.0.0.1:8700"\ntelegram_bot_token=""\ntelegram_allowed_chat_ids=[]\n')
    (tmp_path/"ws").mkdir()
    from fastapi.testclient import TestClient
    from agent.main import build_app_from_config
    html = TestClient(build_app_from_config(str(cfg))).get("/").text
    assert "Mark outdated" in html and "Clear all" in html


def test_index_sends_session_id(tmp_path):
    cfg = tmp_path/"config.toml"
    cfg.write_text('ollama_base_url="http://x/v1"\ndefault_model="gpt-oss"\n'
        f'workdir="{tmp_path/"ws"}"\nalways_ask=[]\nmax_steps=5\ncommand_timeout=10\n'
        'bind="127.0.0.1:8700"\ntelegram_bot_token=""\ntelegram_allowed_chat_ids=[]\n')
    (tmp_path/"ws").mkdir()
    from fastapi.testclient import TestClient
    from agent.main import build_app_from_config
    html = TestClient(build_app_from_config(str(cfg))).get("/").text
    assert "session_id" in html


def test_index_has_mcp_ui(tmp_path):
    cfg = tmp_path/"config.toml"
    cfg.write_text('ollama_base_url="http://x/v1"\ndefault_model="gpt-oss"\n'
        f'workdir="{tmp_path/"ws"}"\nalways_ask=[]\nmax_steps=5\ncommand_timeout=10\n'
        'bind="127.0.0.1:8700"\ntelegram_bot_token=""\ntelegram_allowed_chat_ids=[]\n')
    (tmp_path/"ws").mkdir()
    from fastapi.testclient import TestClient
    from agent.main import build_app_from_config
    html = TestClient(build_app_from_config(str(cfg))).get("/").text
    assert "Add server" in html and "/api/mcp" in html


def test_index_has_mcp_http_form(tmp_path):
    cfg = tmp_path/"config.toml"
    cfg.write_text('ollama_base_url="http://x/v1"\ndefault_model="gpt-oss"\n'
        f'workdir="{tmp_path/"ws"}"\nalways_ask=[]\nmax_steps=5\ncommand_timeout=10\n'
        'bind="127.0.0.1:8700"\ntelegram_bot_token=""\ntelegram_allowed_chat_ids=[]\n')
    (tmp_path/"ws").mkdir()
    from fastapi.testclient import TestClient
    from agent.main import build_app_from_config
    html = TestClient(build_app_from_config(str(cfg))).get("/").text
    assert "mcp-transport" in html and "mcp-url" in html
