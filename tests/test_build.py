from pathlib import Path
from agent.config import Config
from agent.build import build_registry, build_agent
from agent.approval_gate import AutoGate

def _cfg(tmp_path):
    return Config(ollama_base_url="http://x/v1", default_model="gpt-oss", workdir=tmp_path,
                  always_ask=[], max_steps=5, command_timeout=10, bind="127.0.0.1:8700")

def test_registry_has_all_tools(tmp_path):
    names = {t.name for t in build_registry(_cfg(tmp_path)).list()}
    assert {"read_file","list_dir","write_file","edit_file","run_command","web_fetch","web_search"} <= names

def test_mutating_flags_correct(tmp_path):
    reg = build_registry(_cfg(tmp_path))
    assert reg.get("run_command").mutating and reg.get("write_file").mutating
    assert not reg.get("read_file").mutating and not reg.get("web_search").mutating

def test_build_agent_wires_max_steps(tmp_path):
    agent = build_agent(_cfg(tmp_path), AutoGate())
    assert agent.max_steps == 5
