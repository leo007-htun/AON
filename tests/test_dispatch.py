import pytest
from agent.engine import dispatch, Step
from agent.llm_client import ToolCall
from agent.tools.base import Tool, ToolResult, ToolRegistry
from agent.approval_gate import AutoGate, ApprovalGate, PendingAction, Decision

class Recorder(Tool):
    def __init__(self, name, mutating): self.name=name; self.mutating=mutating; self.calls=[]; self.description=""; self.parameters={}
    async def run(self, args): self.calls.append(args); return ToolResult(ok=True, output=f"ran {args}")

def _reg(*tools):
    r = ToolRegistry()
    for t in tools: r.register(t)
    return r

@pytest.mark.asyncio
async def test_readonly_runs_directly():
    t = Recorder("read_file", mutating=False)
    step = await dispatch(ToolCall("1", "read_file", {"path": "a"}), _reg(t), AutoGate(approve=False))
    assert step.approved and t.calls == [{"path": "a"}] and "ran" in step.output

@pytest.mark.asyncio
async def test_mutating_rejected_does_not_run():
    t = Recorder("run_command", mutating=True)
    step = await dispatch(ToolCall("1", "run_command", {"command": "rm x"}), _reg(t), AutoGate(approve=False))
    assert not step.approved and t.calls == [] and "rejected" in step.output.lower()

class EditGate(ApprovalGate):
    async def request(self, action: PendingAction) -> Decision:
        return Decision(approved=True, edited_args={"command": "echo safe"})

@pytest.mark.asyncio
async def test_mutating_approved_uses_edited_args():
    t = Recorder("run_command", mutating=True)
    step = await dispatch(ToolCall("1", "run_command", {"command": "rm x"}), _reg(t), EditGate())
    assert step.approved and t.calls == [{"command": "echo safe"}] and step.args == {"command": "echo safe"}

@pytest.mark.asyncio
async def test_unknown_tool_is_error_not_raise():
    step = await dispatch(ToolCall("1", "nope", {}), _reg(), AutoGate(approve=True))
    assert not step.approved and "unknown tool" in step.output.lower()
