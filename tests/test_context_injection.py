import pytest
from agent.prompts import build_messages
from agent.engine import Agent
from agent.llm_client import ChatResult
from agent.tools.base import ToolRegistry
from agent.approval_gate import AutoGate
from agent.memory import MemoryStore
from agent.skills import SkillStore

def test_build_messages_injects(tmp_path):
    msgs = build_messages("g", [], memory_text="- fact A", skill_list=[("deploy","Deploy steps")])
    sys = msgs[0]["content"]
    assert "fact A" in sys and "deploy" in sys and "read_skill" in sys.lower()

@pytest.mark.asyncio
async def test_agent_reads_stores(tmp_path):
    mem = MemoryStore(tmp_path/"m.md"); mem.append("remember me")
    sk = SkillStore(tmp_path/"sk"); sk.write("s1","First skill\nbody")
    captured = {}
    class LLM:
        async def chat(self, messages, tools=None):
            captured["sys"] = messages[0]["content"]; return ChatResult("done", [])
    await Agent(LLM(), ToolRegistry(), AutoGate(), memory=mem, skills=sk).run("go")
    assert "remember me" in captured["sys"] and "s1" in captured["sys"]
