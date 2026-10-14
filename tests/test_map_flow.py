import json, pytest
from fastapi.testclient import TestClient
from agent.router import deliberate, Plan, _SYS
from agent.engine import Agent, RunResult
from agent.geo import MapResult
from agent.llm_client import ChatResult, ToolCall
from agent.tools.base import ToolRegistry
from agent.tools.map_tool import ShowMapTool
from agent.approval_gate import AutoGate
from agent.config import Config
from agent.persistence import Database
from agent.web import create_app

# ---- planner ----

class JLLM:
    def __init__(self, obj): self.obj = obj
    async def chat(self, m, tools=None): return ChatResult(json.dumps(self.obj), [])

def test_planner_prompt_describes_map_field():
    assert '"map"' in _SYS and "route" in _SYS and "target" in _SYS

@pytest.mark.asyncio
async def test_planner_map_field_is_normalised():
    p = await deliberate("where is albert dock", JLLM({"intent": "answer", "direct_answer": "Liverpool.",
                         "map": {"mode": "target", "places": ["Albert Dock, Liverpool"]}}))
    assert p.map == {"mode": "target", "places": ["Albert Dock, Liverpool"], "travel": "drive", "view": "auto", "orbit": None}
    q = await deliberate("fix my code", JLLM({"intent": "task", "map": None}))
    assert q.map is None
    r = await deliberate("x", JLLM({"intent": "task", "map": {"mode": "nonsense"}}))
    assert r.map is None

def test_plan_map_defaults_to_none():
    assert Plan("", "answer", [], False, None, "hi").map is None

# ---- engine ----

class LLM:
    def __init__(self, seq): self.seq = seq; self.i = 0; self.seen = []
    async def chat(self, m, tools=None):
        self.seen.append(m); r = self.seq[min(self.i, len(self.seq) - 1)]; self.i += 1; return r

def _router(plan):
    async def r(goal, conversation=None): return plan
    return r

def _mapper(result, calls):
    async def m(spec): calls.append(spec); return result
    return m

SPEC = {"mode": "target", "places": ["Albert Dock"], "travel": "drive", "view": "auto", "orbit": None}
EV = {"type": "map", "action": "new", "mode": "target", "view": "3d", "orbit": True, "targets": [{"name": "Royal Albert Dock"}]}

@pytest.mark.asyncio
async def test_map_event_emitted_before_direct_answer():
    events, calls = [], []
    async def emit(e): events.append(e)
    plan = Plan("", "answer", [], False, None, "It is in Liverpool."); plan.map = SPEC
    res = await Agent(LLM([ChatResult("x", [])]), ToolRegistry(), AutoGate(), on_event=emit,
                      router=_router(plan), mapper=_mapper(MapResult(event=EV, context="ctx"), calls)).run("where is albert dock")
    assert calls == [SPEC] and res.final == "It is in Liverpool."
    kinds = [e["type"] for e in events]
    assert kinds.index("map") < kinds.index("final")

@pytest.mark.asyncio
async def test_route_without_origin_asks_for_start():
    events, calls = [], []
    async def emit(e): events.append(e)
    plan = Plan("", "task", [], False, None, None); plan.map = SPEC | {"mode": "route"}
    res = await Agent(LLM([ChatResult("x", [])]), ToolRegistry(), AutoGate(), on_event=emit, router=_router(plan),
                      mapper=_mapper(MapResult(event=EV, ask="Where are you starting from?"), calls)).run("route to albert dock")
    assert res.status == "clarify" and "starting from" in res.final
    assert any(e["type"] == "map" for e in events)

@pytest.mark.asyncio
async def test_map_context_and_notices_reach_loop_and_ui():
    events, calls = [], []
    async def emit(e): events.append(e)
    llm = LLM([ChatResult("Here you go.", [])])
    plan = Plan("", "task", [], False, None, None); plan.map = SPEC
    await Agent(llm, ToolRegistry(), AutoGate(), on_event=emit, router=_router(plan),
                mapper=_mapper(MapResult(event=EV, context="A map is shown. Dock: 53.3995, -2.9925.", notices=["Map: could not find 'X'."]), calls)).run("show dock and X")
    assert "53.3995" in llm.seen[0][0]["content"]
    assert [e for e in events if e["type"] == "notice"][0]["text"].startswith("Map:")

@pytest.mark.asyncio
async def test_mapper_failure_does_not_break_the_run():
    async def boom(spec): raise RuntimeError("nominatim down")
    plan = Plan("", "answer", [], False, None, "Liverpool."); plan.map = SPEC
    res = await Agent(LLM([ChatResult("x", [])]), ToolRegistry(), AutoGate(), router=_router(plan), mapper=boom).run("where")
    assert res.final == "Liverpool."

@pytest.mark.asyncio
async def test_no_mapper_call_without_map_field():
    calls = []
    plan = Plan("", "answer", [], False, None, "hi")
    await Agent(LLM([ChatResult("x", [])]), ToolRegistry(), AutoGate(), router=_router(plan), mapper=_mapper(MapResult(), calls)).run("hi")
    assert calls == []

# ---- backup tool: show_map ----

class FakeGeo:
    async def geocode(self, q): return {"query": q, "name": q, "display": q + ", UK", "lat": 53.4, "lon": -3.0, "kind": "city", "bbox": None}
    async def route(self, a, b, t): return None

@pytest.mark.asyncio
async def test_show_map_tool_returns_coords_and_event():
    t = ShowMapTool(FakeGeo())
    assert not t.mutating and "places" in t.parameters["properties"]
    r = await t.run({"places": ["Liverpool"]})
    assert r.ok and "53.4" in r.output and r.event["type"] == "map"

@pytest.mark.asyncio
async def test_engine_forwards_tool_events_to_ui():
    events = []
    async def emit(e): events.append(e)
    reg = ToolRegistry(); reg.register(ShowMapTool(FakeGeo()))
    llm = LLM([ChatResult("", [ToolCall("1", "show_map", {"places": ["Liverpool"]})]), ChatResult("Mapped.", [])])
    res = await Agent(llm, reg, AutoGate(), on_event=emit).run("map liverpool")
    assert res.final == "Mapped." and any(e["type"] == "map" for e in events)

# ---- /map endpoint ----

def _app(tmp, geo):
    cfg = Config("http://x/v1", "m", tmp, [], 5, 10, "127.0.0.1:8700")
    def ma(*a, **k): raise AssertionError("agent must not run for /map")
    return create_app(cfg, Database(str(tmp / "d.db")), ma, geo=geo)

def test_map_endpoint_builds_event_without_the_agent(tmp_path):
    c = TestClient(_app(tmp_path, FakeGeo()))
    r = c.post("/api/map", json={"text": "/map Tokyo 3d orbit"}).json()
    assert r["event"]["type"] == "map" and r["event"]["view"] == "3d" and r["event"]["orbit"] is True
    assert c.post("/api/map", json={"text": "/map"}).json()["error"]

@pytest.mark.asyncio
async def test_pure_map_update_confirms_without_the_loop():
    events = []
    async def emit(e): events.append(e)
    llm = LLM([ChatResult("", [])])            # the loop would return nothing
    plan = Plan("", "task", [], False, None, None)
    plan.map = {"mode": "update", "places": [], "travel": "drive", "view": "3d", "orbit": True}
    ev = {"type": "map", "action": "update", "view": "3d", "orbit": True}
    res = await Agent(llm, ToolRegistry(), AutoGate(), on_event=emit, router=_router(plan),
                      mapper=_mapper(MapResult(event=ev, context="The map already shown was updated."), [])).run("switch it to 3D and spin it")
    assert res.status == "done" and "3D" in res.final and "orbit" in res.final.lower()
    assert llm.seen == [] and any(e["type"] == "map" for e in events)

# ---- route from the browser's location ----

ROUTE_SPEC = {"mode": "route", "places": ["Formby station"], "travel": "drive", "view": "auto", "orbit": None}
ROUTE_EV = {"type": "map", "action": "new", "mode": "route", "routes": {"walk": {}, "drive": {}, "cycle": {}}}

def _route_mapper(calls):
    async def m(spec, origin=None):
        calls.append(origin)
        if origin is None:
            return MapResult(event={"type": "map", "action": "new", "mode": "target"}, need_location=True,
                             ask="Where are you starting from?")
        return MapResult(event=ROUTE_EV, context="route ctx", summary="Route from Your location to Formby Station:\n- Walk: 5.20 km · 1 h 4 min")
    return m

@pytest.mark.asyncio
async def test_route_uses_browser_location_and_answers_with_summary():
    events, calls = [], []
    async def emit(e): events.append(e)
    async def locator(): return {"lat": 53.43, "lon": -2.95}
    llm = LLM([ChatResult("should not be used", [])])
    plan = Plan("", "task", [], False, None, None); plan.map = ROUTE_SPEC
    a = Agent(llm, ToolRegistry(), AutoGate(), on_event=emit, router=_router(plan), mapper=_route_mapper(calls))
    a.locator = locator
    res = await a.run("how can I get to formby station?")
    assert calls == [{"lat": 53.43, "lon": -2.95}]           # location first, then one build
    assert res.status == "done" and res.final.startswith("Route from Your location") and llm.seen == []
    assert [e for e in events if e["type"] == "map"][-1]["mode"] == "route"

@pytest.mark.asyncio
async def test_route_falls_back_to_asking_when_location_unavailable():
    calls = []
    async def locator(): return None
    plan = Plan("", "task", [], False, None, None); plan.map = ROUTE_SPEC
    a = Agent(LLM([ChatResult("x", [])]), ToolRegistry(), AutoGate(), router=_router(plan), mapper=_route_mapper(calls))
    a.locator = locator
    res = await a.run("how can I get to formby station?")
    assert res.status == "clarify" and "starting from" in res.final and calls == [None]

@pytest.mark.asyncio
async def test_route_without_locator_still_asks():
    plan = Plan("", "task", [], False, None, None); plan.map = ROUTE_SPEC
    res = await Agent(LLM([ChatResult("x", [])]), ToolRegistry(), AutoGate(), router=_router(plan), mapper=_route_mapper([])).run("route")
    assert res.status == "clarify"

class LocAgent:
    def __init__(self, emit, seen): self.emit = emit; self.seen = seen; self.locator = None
    async def run(self, goal, conversation=None):
        self.seen["loc"] = await self.locator()
        await self.emit({"type": "final", "status": "done", "final": "ok"})
        return RunResult("done", "ok", [])

def _loc_app(tmp, seen):
    cfg = Config("http://x/v1", "m", tmp, [], 5, 10, "127.0.0.1:8700")
    return create_app(cfg, Database(str(tmp / "d.db")), lambda g, e, m, c="default": LocAgent(e, seen))

def test_ws_location_round_trip(tmp_path):
    seen = {}
    from fastapi.testclient import TestClient as TC
    with TC(_loc_app(tmp_path, seen)).websocket_connect("/ws") as ws:
        ws.send_json({"type": "goal", "goal": "route me", "model": "m"})
        assert ws.receive_json()["type"] == "need_location"
        ws.send_json({"type": "location", "lat": 53.43, "lon": -2.95, "accuracy": 30})
        while ws.receive_json()["type"] != "final": pass
    assert seen["loc"]["lat"] == 53.43 and seen["loc"]["lon"] == -2.95

def test_ws_location_refused_or_bogus_is_none(tmp_path):
    from fastapi.testclient import TestClient as TC
    for reply in [{"type": "location", "error": "denied"}, {"type": "location", "lat": 999, "lon": 0}, {"type": "location", "lat": "x", "lon": 1}]:
        seen = {}
        with TC(_loc_app(tmp_path, seen)).websocket_connect("/ws") as ws:
            ws.send_json({"type": "goal", "goal": "route me", "model": "m"})
            assert ws.receive_json()["type"] == "need_location"
            ws.send_json(reply)
            while ws.receive_json()["type"] != "final": pass
        assert seen["loc"] is None, reply

@pytest.mark.asyncio
async def test_location_rebuild_failure_keeps_target_map_and_asks():
    events = []
    async def emit(e): events.append(e)
    async def locator(): return {"lat": 53.43, "lon": -2.95}
    async def mapper(spec, origin=None):
        if origin is not None:
            raise RuntimeError("routing service down")
        return MapResult(event={"type": "map", "action": "new", "mode": "target"}, need_location=True, ask="Where are you starting from?")
    plan = Plan("", "answer", [], False, None, "made-up planner answer"); plan.map = ROUTE_SPEC
    a = Agent(LLM([ChatResult("x", [])]), ToolRegistry(), AutoGate(), on_event=emit, router=_router(plan), mapper=mapper)
    a.locator = locator
    res = await a.run("how can I get to formby station?")
    assert res.status == "clarify" and "starting from" in res.final
    assert any(e["type"] == "map" for e in events)

@pytest.mark.asyncio
async def test_route_without_start_locates_before_resolving():
    order = []
    async def locator(): order.append("locate"); return {"lat": 53.43, "lon": -2.95}
    async def mapper(spec, origin=None):
        order.append(("map", origin is not None))
        return MapResult(event=ROUTE_EV, summary="Route from Your location to X:\n- Walk: 1 km · 10 min")
    plan = Plan("", "task", [], False, None, None); plan.map = ROUTE_SPEC
    a = Agent(LLM([ChatResult("x", [])]), ToolRegistry(), AutoGate(), router=_router(plan), mapper=mapper)
    a.locator = locator
    res = await a.run("how can I get to x?")
    assert order == ["locate", ("map", True)] and res.status == "done"

@pytest.mark.asyncio
async def test_route_with_named_start_uses_location_only_for_ranking():
    seen = []
    async def locator(): return {"lat": 53.43, "lon": -2.95}
    async def mapper(spec, origin=None):
        seen.append((list(spec["places"]), origin))
        return MapResult(event=ROUTE_EV, summary="Route from Lime Street to X:\n- Walk: 1 km · 10 min")
    plan = Plan("", "task", [], False, None, None)
    plan.map = {**ROUTE_SPEC, "places": ["Lime Street", "Formby station"]}
    a = Agent(LLM([ChatResult("x", [])]), ToolRegistry(), AutoGate(), router=_router(plan), mapper=mapper)
    a.locator = locator
    res = await a.run("from lime street to formby station")
    assert res.status == "done" and res.final.startswith("Route from Lime Street")
    assert seen == [(["Lime Street", "Formby station"], {"lat": 53.43, "lon": -2.95})]   # start kept, location passed

def test_ws_stores_choices_with_a_place_question(tmp_path):
    from agent.geo import parse_choices
    class Q:
        def __init__(self, emit): self.emit = emit
        async def run(self, goal, conversation=None):
            await self.emit({"type": "map", "action": "new", "mode": "area", "pending": {"mode": "route", "travel": "walk", "from": None},
                             "targets": [{"name": "Crewe", "display": "Crewe, Cheshire East", "lat": 53.089, "lon": -2.433}]})
            await self.emit({"type": "final", "status": "clarify", "final": "Do you mean Crewe?"})
            return RunResult("clarify", "Do you mean Crewe?", [])
    db = Database(str(tmp_path / "d.db"))
    app = create_app(Config("http://x/v1", "m", tmp_path, [], 5, 10, "127.0.0.1:8700"), db, lambda g, e, m, c="default": Q(e))
    from fastapi.testclient import TestClient as TC
    with TC(app).websocket_connect("/ws") as ws:
        ws.send_json({"type": "goal", "goal": "how to get to crewe station, liverpool?", "model": "m", "session_id": "s"})
        while ws.receive_json()["type"] != "final": pass
    stored = db.recent_turns("s", 5)[-1]["content"]
    ch = parse_choices(stored)
    assert ch["mode"] == "route" and ch["travel"] == "walk" and ch["options"][0] == {"name": "Crewe", "lat": 53.089, "lon": -2.433}

# ---- speed: the narrow extractor first, the big planner only when needed ----

@pytest.mark.asyncio
async def test_map_message_skips_the_planner_when_extractor_finds_a_map(tmp_path):
    from agent.build import make_router
    from agent.config import Config
    from agent.tools.base import ToolRegistry as TR
    calls = []
    class L:
        async def chat(self, m, tools=None):
            sysmsg = m[0]["content"]
            calls.append("extractor" if sysmsg.startswith("Extract the map request") else "planner")
            if calls[-1] == "extractor":
                return ChatResult(json.dumps({"map": {"mode": "route", "from": None, "to": "Formby station"}}), [])
            return ChatResult(json.dumps({"intent": "answer", "direct_answer": "slow planner"}), [])
    cfg = Config("http://x/v1", "m", tmp_path, [], 5, 10, "127.0.0.1:8700")
    router = make_router(cfg, L(), TR())
    p = await router("how can I get to formby station?", None)
    assert calls == ["extractor"] and p.map["places"] == ["Formby station"] and not p.direct_answer
    calls.clear()
    p2 = await router("write a haiku about rain", None)
    assert calls == ["planner"]

# ---- stop button: cancel the run (closing the Ollama request stops GPU generation) ----

def test_ws_stop_cancels_the_run_and_reports_stopped(tmp_path):
    import asyncio, time as _t
    state = {"cancelled": False}
    class Slow:
        def __init__(self, emit): self.emit = emit
        async def run(self, goal, conversation=None):
            try:
                await asyncio.sleep(60)                       # stands in for a long model call
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise
    db = Database(str(tmp_path / "d.db"))
    app = create_app(Config("http://x/v1", "m", tmp_path, [], 5, 10, "127.0.0.1:8700"), db, lambda g, e, m, c="default": Slow(e))
    from fastapi.testclient import TestClient as TC
    t0 = _t.time()
    with TC(app).websocket_connect("/ws") as ws:
        ws.send_json({"type": "goal", "goal": "write a very long story", "model": "m", "session_id": "s"})
        ws.send_json({"type": "stop"})
        m = ws.receive_json()
        assert m["type"] == "final" and m["status"] == "stopped"
    assert _t.time() - t0 < 10 and state["cancelled"]
    turns = db.recent_turns("s", 5)
    assert turns[-1]["role"] == "assistant" and "stopped" in turns[-1]["content"].lower()
