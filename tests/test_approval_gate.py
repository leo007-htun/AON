import asyncio, pytest
from aon.approval_gate import PendingAction, Decision, AutoGate, QueueGate

@pytest.mark.asyncio
async def test_autogate_approves():
    d = await AutoGate(approve=True).request(PendingAction("run_command", {"command": "ls"}))
    assert d.approved and d.edited_args is None

@pytest.mark.asyncio
async def test_autogate_rejects():
    d = await AutoGate(approve=False).request(PendingAction("run_command", {"command": "rm -rf /"}))
    assert not d.approved

@pytest.mark.asyncio
async def test_queuegate_parks_and_resolves():
    gate = QueueGate()
    task = asyncio.create_task(gate.request(PendingAction("write_file", {"path": "a"})))
    await asyncio.sleep(0.01)
    pend = gate.pending()
    assert len(pend) == 1 and pend[0][1].tool == "write_file"
    gate.resolve(pend[0][0], Decision(approved=True, edited_args={"path": "b"}))
    d = await asyncio.wait_for(task, timeout=1)
    assert d.approved and d.edited_args == {"path": "b"}
