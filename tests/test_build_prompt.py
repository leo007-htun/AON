from pathlib import Path
from agent.config import Config
from agent.build import build_agent
from agent.approval_gate import AutoGate
from agent.prompts import save_system_prompt

def test_build_agent_loads_saved_prompt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_system_prompt(Path("system_prompt.txt"), "AON custom prompt")
    (tmp_path/"ws").mkdir()
    cfg = Config("http://x/v1","gpt-oss",tmp_path/"ws",[],5,10,"127.0.0.1:8700")
    a = build_agent(cfg, AutoGate())
    assert a.system_prompt == "AON custom prompt"
