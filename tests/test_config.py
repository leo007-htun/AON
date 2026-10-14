from pathlib import Path
import pytest
from agent.config import load_config, Config, GUARDRAIL_PATHS

def test_loads_valid_config(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        'ollama_base_url = "http://x:11434/v1"\n'
        'default_model = "gpt-oss"\n'
        'workdir = "' + str(tmp_path / "ws") + '"\n'
        'always_ask = ["bash"]\n'
        'max_steps = 10\n'
        'command_timeout = 30\n'
        'bind = "127.0.0.1:8700"\n'
        'telegram_bot_token = "tok"\n'
        'telegram_allowed_chat_ids = [123]\n'
    )
    c = load_config(p)
    assert isinstance(c, Config)
    assert c.default_model == "gpt-oss"
    assert c.workdir == (tmp_path / "ws")
    assert c.always_ask == ["bash"]
    assert c.telegram_allowed_chat_ids == [123]

def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")

def test_guardrail_paths_present():
    assert "src/agent/executor.py" in GUARDRAIL_PATHS
    assert "src/agent/approval_gate.py" in GUARDRAIL_PATHS
