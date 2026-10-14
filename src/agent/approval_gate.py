# GUARDRAIL MODULE -- not editable by agent tools (see config.GUARDRAIL_PATHS).
from __future__ import annotations
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class PendingAction:
    tool: str
    args: dict

@dataclass
class Decision:
    approved: bool
    edited_args: dict | None = None
    reason: str = ""

class ApprovalGate(ABC):
    @abstractmethod
    async def request(self, action: PendingAction) -> Decision: ...

class AutoGate(ApprovalGate):
    def __init__(self, approve: bool = True):
        self._approve = approve
    async def request(self, action: PendingAction) -> Decision:
        return Decision(approved=self._approve, reason="auto")

class QueueGate(ApprovalGate):
    def __init__(self, on_pending=None):
        self._next = 1
        self._pending: dict[int, tuple[PendingAction, asyncio.Future]] = {}
        self._on_pending = on_pending
    async def request(self, action: PendingAction) -> Decision:
        rid = self._next; self._next += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = (action, fut)
        if self._on_pending is not None:
            await self._on_pending(rid, action)
        try:
            return await fut
        finally:
            self._pending.pop(rid, None)
    def pending(self) -> list[tuple[int, PendingAction]]:
        return [(rid, pa) for rid, (pa, _f) in self._pending.items()]
    def resolve(self, req_id: int, decision: Decision) -> None:
        entry = self._pending.get(req_id)
        if entry and not entry[1].done():
            entry[1].set_result(decision)
