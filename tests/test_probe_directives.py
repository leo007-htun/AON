from agent.prompts import SYSTEM_PROMPT
from agent.router import _SYS


def test_system_prompt_probe():
    assert "which" in SYSTEM_PROMPT and "Never claim" in SYSTEM_PROMPT and "remember" in SYSTEM_PROMPT


def test_planner_verify_step():
    assert "verif" in _SYS.lower() and ("first step" in _SYS.lower() or "verification" in _SYS.lower())
