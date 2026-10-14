from aon.config import Config
from aon.build import build_agent, build_eval_agent
from aon.approval_gate import AutoGate


def _cfg(tmp):
    return Config("http://x/v1", "m", tmp, [], 5, 10, "127.0.0.1:8700")


def test_build_agent_has_router(tmp_path):
    assert build_agent(_cfg(tmp_path), AutoGate()).router is not None


def test_eval_agent_has_no_router(tmp_path):
    (tmp_path / "ws").mkdir()
    a = build_eval_agent(_cfg(tmp_path), tmp_path / "ws", tmp_path / "sk", "m", object())
    assert a.router is None
