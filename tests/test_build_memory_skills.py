from pathlib import Path
from agent.config import Config
from agent.build import build_registry, build_agent
from agent.approval_gate import AutoGate

def _cfg(tmp): return Config("http://x/v1","gpt-oss",tmp,[],5,10,"127.0.0.1:8700")

def test_registry_includes_memory_skills(tmp_path):
    names = {t.name for t in build_registry(_cfg(tmp_path)).list()}
    assert {"remember","read_skill","write_skill"} <= names

def test_build_agent_has_stores(tmp_path):
    a = build_agent(_cfg(tmp_path), AutoGate())
    assert a.memory is not None and a.skills is not None
