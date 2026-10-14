import asyncio, pytest
from aon.approval_gate import QueueGate, PendingAction, Decision
from aon.engine import Agent
from aon.llm_client import ChatResult, ToolCall
from aon.tools.base import Tool, ToolResult, ToolRegistry
from aon.approval_gate import AutoGate

@pytest.mark.asyncio
async def test_queuegate_calls_on_pending():
    seen = []
    async def hook(rid, action): seen.append((rid, action.tool))
    gate = QueueGate(on_pending=hook)
    task = asyncio.create_task(gate.request(PendingAction("run_command", {"command":"ls"})))
    await asyncio.sleep(0.01)
    assert seen and seen[0][1] == "run_command"
    gate.resolve(seen[0][0], Decision(approved=True))
    await asyncio.wait_for(task, 1)

def test_resolve_unknown_id_is_noop():
    QueueGate().resolve(999, Decision(approved=True))

class Echo(Tool):
    name="read_file"; description=""; parameters={}; mutating=False
    async def run(self, args): return ToolResult(ok=True, output="x")
class FakeLLM:
    def __init__(self, s): self.s=list(s); self.i=0
    async def chat(self, m, tools=None):
        r=self.s[min(self.i,len(self.s)-1)]; self.i+=1; return r

@pytest.mark.asyncio
async def test_agent_emits_events():
    events=[]
    async def on_event(e): events.append(e["type"])
    reg=ToolRegistry(); reg.register(Echo())
    llm=FakeLLM([ChatResult("",[ToolCall("1","read_file",{"path":"a"})]), ChatResult("done",[])])
    await Agent(llm, reg, AutoGate(), max_steps=5, on_event=on_event).run("go")
    assert "step" in events and events[-1] == "final"
