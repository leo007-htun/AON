import pytest
from pathlib import Path
from aon.prompts import SYSTEM_PROMPT, load_system_prompt, save_system_prompt, build_messages
from aon.engine import Agent
from aon.llm_client import ChatResult
from aon.tools.base import ToolRegistry
from aon.approval_gate import AutoGate

def test_load_default_when_missing(tmp_path):
    assert load_system_prompt(tmp_path/"nope.txt") == SYSTEM_PROMPT

def test_save_then_load(tmp_path):
    p = tmp_path/"sp.txt"; save_system_prompt(p, "You are AON.")
    assert load_system_prompt(p) == "You are AON."

def test_build_messages_uses_custom_prompt():
    msgs = build_messages("g", [], system_prompt="CUSTOM-AON-PROMPT")
    assert msgs[0]["content"].startswith("CUSTOM-AON-PROMPT")

@pytest.mark.asyncio
async def test_agent_uses_custom_prompt():
    cap = {}
    class LLM:
        async def chat(self, m, tools=None): cap["s"]=m[0]["content"]; return ChatResult("done",[])
    await Agent(LLM(), ToolRegistry(), AutoGate(), system_prompt="AON-SYS").run("go")
    assert cap["s"].startswith("AON-SYS")
