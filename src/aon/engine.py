from __future__ import annotations
import json
import re
from dataclasses import dataclass
from aon.llm_client import ToolCall
from aon.tools.base import ToolRegistry
from aon.approval_gate import ApprovalGate, PendingAction
from aon.prompts import build_messages
from aon.router import render_step, plan_progress
from aon.context import fit_messages, fit_exact
from aon.memory_extract import is_install_command

@dataclass
class Step:
    tool: str
    args: dict
    output: str
    approved: bool
    event: dict | None = None

# --- resilience: some local models emit a tool call as JSON in the message
# content instead of the API tool_calls field. Recover those so the call runs
# instead of being printed as the final answer. ---

_NAME_KEYS = ("name", "command", "tool", "action")
_ARG_KEYS = ("arguments", "args", "parameters", "params", "input")

def _brace_fragments(text: str) -> list[str]:
    out: list[str] = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                out.append(text[start:i + 1])
                start = None
    return out

def _coerce_call(obj, registry: ToolRegistry, idx: int):
    if not isinstance(obj, dict):
        return None
    fn = obj.get("function") if isinstance(obj.get("function"), dict) else obj
    name = next((fn[k] for k in _NAME_KEYS if isinstance(fn.get(k), str)), None)
    if not name:
        return None
    try:
        registry.get(name)
    except KeyError:
        return None
    args: dict = {}
    for k in _ARG_KEYS:
        if k in fn:
            a = fn[k]
            if isinstance(a, str):
                try:
                    a = json.loads(a)
                except json.JSONDecodeError:
                    a = {}
            if isinstance(a, dict):
                args = a
            break
    return ToolCall(id=f"recovered-{idx}", name=name, arguments=args)

def recover_tool_calls(content: str, registry: ToolRegistry) -> list[ToolCall]:
    """Parse tool calls a model emitted as JSON in its text content.

    Only attempts recovery when the content is itself a JSON/code block (starts
    with '{', '[' or a code fence), to avoid misreading prose that merely
    mentions a tool name. A JSON object is recovered only when its name field
    matches a registered tool.
    """
    if not content:
        return []
    stripped = content.lstrip()
    if not (stripped.startswith("{") or stripped.startswith("[")
            or stripped.startswith("```") or "```" in content):
        return []
    calls: list[ToolCall] = []
    for frag in _brace_fragments(content):
        try:
            obj = json.loads(frag)
        except json.JSONDecodeError:
            continue
        items = obj if isinstance(obj, list) else [obj]
        for it in items:
            c = _coerce_call(it, registry, len(calls))
            if c:
                calls.append(c)
    return calls

async def dispatch(call: ToolCall, registry: ToolRegistry, gate: ApprovalGate) -> Step:
    try:
        tool = registry.get(call.name)
    except KeyError:
        return Step(call.name, call.arguments, f"error: unknown tool '{call.name}'", approved=False)
    args = call.arguments
    if tool.mutating:
        decision = await gate.request(PendingAction(call.name, args))
        if not decision.approved:
            return Step(call.name, args, f"rejected by user: {decision.reason}", approved=False)
        args = decision.edited_args or args
    try:
        res = await tool.run(args)
    except Exception as e:
        detail = f"missing argument {e}" if isinstance(e, KeyError) else f"{type(e).__name__}: {e}"
        return Step(call.name, args, f"error: {call.name} failed ({detail})", approved=True)
    out = res.output if res.ok else f"error: {res.error}"
    if getattr(res, "event", None):
        return Step(call.name, args, out, approved=True, event=res.event)
    return Step(call.name, args, out, approved=True)

_ACTION_CLAIM = re.compile(
    r"\b(has|have|was|were|is now|are now)\s+(been\s+)?(added|installed|created|connected|configured|"
    r"saved|sent|registered|deleted|updated|deployed|written|imported|set up)\b"
    r"|\bI(?:'ve| have)\s+(added|installed|created|connected|configured|saved|sent|registered|"
    r"deleted|updated|imported|set up)\b", re.I)


def _claims_action(text: str) -> bool:
    return bool(_ACTION_CLAIM.search(text or ""))


@dataclass
class RunResult:
    status: str
    final: str
    steps: list

class Agent:
    def __init__(self, llm, registry: ToolRegistry, gate: ApprovalGate, max_steps: int = 40,
                 repeat_limit: int = 3, on_event=None, memory=None, skills=None, system_prompt=None,
                 router=None, intake=None, mapper=None, context_limit=None):
        self.llm = llm; self.registry = registry; self.gate = gate
        self.max_steps = max_steps; self.repeat_limit = repeat_limit; self.on_event = on_event
        self.memory = memory; self.skills = skills; self.system_prompt = system_prompt
        self.router = router
        self.intake = intake
        self.mapper = mapper
        self.context_limit = context_limit   # max tokens per request (None: no fitting)
        self.locator = None   # set per connection by the web layer: async () -> {"lat","lon"} | None

    async def _emit(self, event: dict):
        if self.on_event is not None:
            await self.on_event(event)

    def _memory_text(self, goal: str) -> str:
        if not self.memory:
            return ""
        ctx = getattr(self.memory, "context_for", None)
        return ctx(goal) if ctx else self.memory.read()

    async def _finish(self, status: str, final: str, steps: list) -> RunResult:
        await self._emit({"type": "final", "status": status, "final": final})
        return RunResult(status, final, steps)

    async def run(self, goal: str, conversation=None, images=None, background="") -> RunResult:
        # background: what the chat database knows beyond the recent turns (summary, recalled turns)
        history: list[dict] = []
        if conversation:
            for t in conversation:
                history.append({"role": t["role"], "content": t["content"]})
        trail: list[dict] = []   # this run's tool calls/results; sent after the goal
        steps: list[Step] = []
        counts: dict = {}
        plan_steps: list = []
        plan_understanding = ""
        plan_intent = ""
        map_note = ""
        prepare = getattr(self.memory, "prepare", None)
        if prepare is not None:
            # memory search by meaning is async; it runs once here and the memory block reuses it
            try:
                await prepare(goal)
            except Exception:
                pass
        # link intake and the planner only see text; with images attached they would answer blind
        routed = bool(images)
        if self.intake is not None and not routed:
            # a shared link is fetched and digested first; the user picks what to do with it
            try:
                ir = await self.intake(goal, emit=self._emit)
            except Exception:
                ir = None
            if ir is not None:
                if ir.ask:
                    return await self._finish("clarify", ir.ask, steps)
                if ir.final:
                    return await self._finish("done", ir.final, steps)
                if ir.goal:
                    goal, plan_intent, routed = ir.goal, ir.intent, True
        if self.router is not None and not routed:
            try:
                pc = ([{"role": "system", "content": background}] if background else []) + list(conversation or [])
                plan = await self.router(goal, pc)
            except Exception:
                plan = None
            if plan is not None and getattr(plan, "map", None) and self.mapper is not None:
                # a map request shows the map first (on every path), grounded in real coordinates
                # a route with no start: get the user's location FIRST, so places are resolved (and
                # namesakes ranked) relative to where they are; any failure falls back to asking
                spec, mr, located = plan.map, None, False
                # (for every route: with a named start the location still ranks namesakes, e.g. the
                # Brunswick station near the user rather than the one in Maine)
                if spec.get("mode") == "route" and self.locator is not None:
                    located = True
                    try:
                        loc = await self.locator()
                    except Exception:
                        loc = None
                    if loc:
                        try:
                            mr = await self.mapper(spec, origin=loc)
                        except Exception:
                            mr = None
                if mr is None:
                    try:
                        mr = await self.mapper(spec)
                    except Exception:
                        mr = None
                if mr is not None and mr.need_location and self.locator is not None and not located:
                    try:
                        loc = await self.locator()
                        if loc:
                            mr = await self.mapper(plan.map, origin=loc) or mr
                    except Exception:
                        pass
                if mr is not None:
                    for n in mr.notices:
                        await self._emit({"type": "notice", "text": n})
                    if mr.event:
                        await self._emit(mr.event)
                    if mr.ask:
                        return await self._finish("clarify", mr.ask, steps)
                    if mr.summary:
                        # distances/times straight from the routing service, not retold by the model
                        return await self._finish("done", mr.summary, steps)
                    map_note = mr.context or ""
                    # "show it in 3D / spin it": nothing for the loop to do, so confirm the change here
                    if (plan.map.get("mode") == "update" and mr.event and not plan.plan
                            and not plan.direct_answer):
                        bits = [{"3d": "3D view", "2d": "2D view", "globe": "globe view"}.get(mr.event.get("view"), "")]
                        if mr.event.get("orbit") is True: bits.append("orbiting")
                        elif mr.event.get("orbit") is False: bits.append("orbit stopped")
                        done_txt = ", ".join(b for b in bits if b) or "done"
                        return await self._finish("done", f"Map updated: {done_txt}.", steps)
            if plan is not None:
                # questions about the user / earlier chat must reach the memory lookup, not stop here
                recall = plan.intent == "recall"
                if plan.needs_clarification and plan.question and not recall:
                    return await self._finish("clarify", plan.question, steps)
                # a planner reply may only answer; if it claims work was done, run the real loop
                if plan.intent == "answer" and plan.direct_answer and not _claims_action(plan.direct_answer):
                    return await self._finish("done", plan.direct_answer, steps)
                plan_steps = [render_step(a) for a in (plan.plan or [])]
                plan_understanding = plan.understanding or ""
                plan_intent = plan.intent or ""
        done = 0
        last_error = None
        for _ in range(self.max_steps):
            mem_text = self._memory_text(goal)
            sk_list = self.skills.list() if self.skills else None
            tool_summary = "\n".join(
                f"- {t.name}: {((t.description or '').splitlines() or [''])[0][:100]}"
                for t in self.registry.list())
            pp = plan_progress(plan_steps, done, last_error, plan_understanding, plan_intent)
            if map_note:
                pp = (pp + "\n\n" if pp else "") + map_note
            messages = build_messages(goal, history, memory_text=mem_text, skill_list=sk_list,
                                      system_prompt=self.system_prompt, tool_summary=tool_summary,
                                      plan_progress=pp, images=images, steps=trail, background=background)
            schemas = self.registry.schemas()
            if self.context_limit:
                counter = getattr(self.llm, "count_tokens", None)
                if counter is not None:
                    messages = await fit_exact(messages, len(history), schemas, self.context_limit, counter)
                else:
                    messages, _ = fit_messages(messages, len(history), schemas, self.context_limit)
            res = await self.llm.chat(messages, tools=schemas)
            if not res.tool_calls:
                recovered = recover_tool_calls(res.content, self.registry)
                if not recovered:
                    if not (res.content or "").strip() and getattr(res, "finish_reason", None) == "length":
                        return await self._finish("stuck", "The reply was cut off at the context limit before "
                                                  "any text came out. Try a shorter question or start a new chat.",
                                                  steps)
                    if not (res.content or "").strip():
                        return await self._finish("stuck", "The model returned an empty reply (no text and no "
                                                  "tool call). Try again or pick another model.", steps)
                    return await self._finish("done", res.content, steps)
                res.tool_calls = recovered
            trail.append({"role": "assistant", "content": res.content or "",
                            "tool_calls": [{"id": c.id, "type": "function",
                                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                                           for c in res.tool_calls]})
            for call in res.tool_calls:
                step = await dispatch(call, self.registry, self.gate)
                steps.append(step)
                trail.append({"role": "tool", "tool_call_id": call.id, "content": step.output})
                await self._emit({"type": "step", "tool": step.tool, "output": step.output, "approved": step.approved})
                if step.event:
                    await self._emit(step.event)
                ok = step.approved and not step.output.startswith("error:")
                if ok:
                    done += 1
                    last_error = None
                else:
                    last_error = step.output
                if (ok and step.tool == "run_command" and self.memory
                        and is_install_command((step.args or {}).get("command", ""))):
                    try:
                        self.memory.add(f"installed via `{(step.args or {}).get('command', '')}`",
                                        type="action", tags="install", source="auto")
                    except Exception:
                        pass
                key = (step.tool, json.dumps(step.args, sort_keys=True))
                counts[key] = counts.get(key, 0) + 1
                if counts[key] >= self.repeat_limit:
                    return await self._finish("stuck",
                        f"Stopped: repeated the same call {self.repeat_limit}x without progress: {step.tool} {step.args}",
                        steps)
        last = steps[-1].output if steps else "no progress"
        return await self._finish("stuck", f"Stopped at step budget ({self.max_steps}). Last: {last}", steps)
