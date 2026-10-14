import json, pytest
from aon.geo import map_hint
from aon.router import ensure_map, Plan
from aon.llm_client import ChatResult

@pytest.mark.parametrize("text", [
    "Where is Albert Dock?", "where's the Liver Building", "How do I get to Albert Dock?",
    "Directions from Lime Street to Albert Dock", "route to the airport", "how far is Leeds from York",
    "cafes near Lime Street", "show Yangon on a map", "Flight route from Manchester to Tokyo",
    "Compare where Yangon, Seoul and Tokyo are", "Show me the Liver Building", "show me Albert Dock"])
def test_map_hint_catches_place_questions(text):
    assert map_hint(text)

@pytest.mark.parametrize("text", [
    "Write a python function that reverses a string", "List the files in the workspace",
    "Remember that my favourite colour is green", "where is the bug in this code?", "fix the route handler in app.py",
    "show me the code", "show me the logs from yesterday", "Show me how to use Python decorators"])
def test_map_hint_ignores_other_requests(text):
    assert not map_hint(text)

def test_map_hint_view_words_only_count_with_a_map_open():
    assert not map_hint("show it in 3D")
    assert map_hint("show it in 3D", map_open=True) and map_hint("switch to globe and spin it", map_open=True)

class LLM:
    def __init__(self, obj): self.obj = obj; self.calls = []
    async def chat(self, m, tools=None): self.calls.append(m); return ChatResult(json.dumps(self.obj), [])

@pytest.mark.asyncio
async def test_ensure_map_extracts_when_planner_missed_it():
    llm = LLM({"map": {"mode": "route", "places": ["Albert Dock"]}})
    p = await ensure_map(Plan("", "answer", [], False, None, "Head south."), "How do I get to Albert Dock?", llm)
    assert p.map["mode"] == "route" and p.map["places"] == ["Albert Dock"] and len(llm.calls) == 1

@pytest.mark.asyncio
async def test_ensure_map_no_extra_call_when_planner_set_it_or_no_hint():
    llm = LLM({"map": {"mode": "target", "places": ["X"]}})
    pl = Plan("", "answer", [], False, None, None, map={"mode": "target", "places": ["Albert Dock"], "travel": "drive", "view": "auto", "orbit": None})
    assert (await ensure_map(pl, "where is albert dock", llm)).map["places"] == ["Albert Dock"]
    assert (await ensure_map(Plan("", "task", [], False, None, None), "list the files", llm)).map is None
    assert llm.calls == []

@pytest.mark.asyncio
async def test_ensure_map_update_uses_conversation_with_open_map():
    llm = LLM({"map": {"mode": "update", "places": [], "view": "3d"}})
    convo = [{"role": "user", "content": "Where is Albert Dock?"}, {"role": "assistant", "content": "On the waterfront. A map is shown."}]
    p = await ensure_map(Plan("", "task", [], False, None, None), "show it in 3D", llm, convo, map_open=True)
    assert p.map["mode"] == "update" and p.map["view"] == "3d"
    assert "Albert Dock" in json.dumps(llm.calls[0])

@pytest.mark.asyncio
async def test_ensure_map_survives_bad_extractor_output():
    class Bad:
        async def chat(self, m, tools=None): return ChatResult("not json", [])
    p = await ensure_map(Plan("", "task", [], False, None, None), "where is albert dock", Bad())
    assert p.map is None

def test_stored_turn_marks_that_a_map_was_shown(tmp_path):
    from fastapi.testclient import TestClient
    from aon.config import Config
    from aon.persistence import Database
    from aon.web import create_app
    from aon.engine import RunResult
    class A:
        def __init__(self, emit): self.emit = emit
        async def run(self, goal, conversation=None):
            await self.emit({"type": "map", "action": "new", "mode": "target", "targets": []})
            await self.emit({"type": "final", "status": "done", "final": "It is in Liverpool."})
            return RunResult("done", "It is in Liverpool.", [])
    db = Database(str(tmp_path / "d.db"))
    app = create_app(Config("http://x/v1", "m", tmp_path, [], 5, 10, "127.0.0.1:8700"), db, lambda g, e, m, c="default": A(e))
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"type": "goal", "goal": "where is albert dock", "model": "m", "session_id": "s"})
        while ws.receive_json()["type"] != "final": pass
    turns = db.recent_turns("s", 5)
    assert turns[-1]["role"] == "assistant" and "[map shown]" in turns[-1]["content"]

# ---- "A in B": the city qualifies the place, it is not a second place ----
from aon.geo import merge_qualifiers

Q = "Where is the Liver Building in Liverpool?"

@pytest.mark.parametrize("places", [["Liver Building", "Liverpool"], ["Liverpool"], ["Liver Building"], ["Liverpool", "Liver Building"]])
def test_merge_qualifiers_makes_one_specific_place(places):
    assert merge_qualifiers(places, Q) == ["Liver Building, Liverpool"]

def test_merge_qualifiers_leaves_real_lists_and_routes_alone():
    assert merge_qualifiers(["Yangon", "Seoul"], "Compare Yangon and Seoul") == ["Yangon", "Seoul"]
    assert merge_qualifiers(["Lime Street", "Albert Dock"], "Directions from Lime Street to Albert Dock in Liverpool") == ["Lime Street", "Albert Dock, Liverpool"]
    assert merge_qualifiers(["Albert Dock, Liverpool"], "Where is Albert Dock in Liverpool?") == ["Albert Dock, Liverpool"]
    assert merge_qualifiers(["Tokyo"], "where is tokyo") == ["Tokyo"]

@pytest.mark.asyncio
async def test_ensure_map_merges_split_city_and_fixes_mode():
    llm = LLM({"map": {"mode": "area", "places": ["Liver Building", "Liverpool"]}})
    p = await ensure_map(Plan("", "task", [], False, None, None), Q, llm)
    assert p.map["places"] == ["Liver Building, Liverpool"] and p.map["mode"] == "target"
    # also applied when the planner itself produced the split
    pl = Plan("", "answer", [], False, None, None, map={"mode": "target", "places": ["Liverpool"], "travel": "drive", "view": "auto", "orbit": None})
    assert (await ensure_map(pl, Q, LLM({}))).map["places"] == ["Liver Building, Liverpool"]

# ---- "Formby station": keep the place-type word the model dropped ----
from aon.geo import expand_place_types

def test_expand_place_types_restores_dropped_type_word():
    assert expand_place_types(["Formby"], "how can I get to formby station?") == ["Formby station"]
    assert expand_place_types(["Liverpool"], "route to Liverpool John Lennon Airport") == ["Liverpool John Lennon Airport"]
    assert expand_place_types(["Sefton"], "walk to sefton park please") == ["Sefton park"]

def test_expand_place_types_leaves_complete_or_unrelated_names():
    assert expand_place_types(["Formby Station"], "how can I get to formby station?") == ["Formby Station"]
    assert expand_place_types(["Tokyo"], "where is tokyo") == ["Tokyo"]
    assert expand_place_types(["Albert Dock, Liverpool"], "Where is Albert Dock in Liverpool?") == ["Albert Dock, Liverpool"]
    assert expand_place_types(["Liverpool"], "drive from Liverpool to the station") == ["Liverpool"]
    assert expand_place_types(["Crosby"], "walk from Crosby to Formby station") == ["Crosby"]

@pytest.mark.asyncio
async def test_ensure_map_applies_place_type_expansion():
    pl = Plan("", "task", [], False, None, None, map={"mode": "route", "places": ["Formby"], "travel": "drive", "view": "auto", "orbit": None})
    assert (await ensure_map(pl, "how can I get to formby station?", LLM({}))).map["places"] == ["Formby station"]

# ---- explicit from/to from the model ----
from aon.geo import normalize_map
from aon.router import _MAP_SYS

def test_normalize_map_accepts_from_to():
    a = normalize_map({"mode": "route", "from": None, "to": "Formby station"})
    assert a["places"] == ["Formby station"] and a["mode"] == "route"
    b = normalize_map({"mode": "route", "from": "Lime Street", "to": "Formby station", "travel": "walk"})
    assert b["places"] == ["Lime Street", "Formby station"] and b["travel"] == "walk"
    assert normalize_map({"mode": "route", "from": "x", "to": ""}) is None

def test_extractor_prompt_asks_for_from_and_to():
    assert '"from"' in _MAP_SYS and '"to"' in _MAP_SYS and "null" in _MAP_SYS

# ---- the user answering one of AON's place questions ----
ASKED = [{"role": "user", "content": "how to get to crewe station, liverpool?"},
         {"role": "assistant", "content": "I couldn't find crewe station in liverpool. The closest match is Crewe in Crewe, Cheshire East (about 55 km from liverpool). Do you mean that one, or a different place in liverpool?\n[map shown]"}]

@pytest.mark.asyncio
async def test_reply_to_place_question_goes_to_the_extractor():
    llm = LLM({"map": {"mode": "route", "from": None, "to": "Crewe station, Crewe"}})
    p = await ensure_map(Plan("", "answer", [], False, None, "ok"), "yes, that one", llm, ASKED, pending_question=True)
    assert p.map["places"] == ["Crewe station, Crewe"] and len(llm.calls) == 1
    assert "Do you mean that one" in json.dumps(llm.calls[0])        # it sees the question it is answering

def test_pending_place_question_detection():
    from aon.router import pending_place_question
    assert pending_place_question(ASKED)
    assert not pending_place_question([{"role": "assistant", "content": "Map updated: 3D view.\n[map shown]"}])
    assert not pending_place_question([{"role": "assistant", "content": "What do you want?"}])
    assert not pending_place_question(None)

# ---- exact answers to "which place?" and full destination phrases ----
from aon.geo import choices_marker, parse_choices, choice_from_reply, recover_route_phrases

EV = {"type": "map", "mode": "area", "pending": {"mode": "route", "travel": "walk", "from": None},
      "targets": [{"name": "Crewe", "display": "Crewe, Cheshire East", "lat": 53.089, "lon": -2.433},
                  {"name": "Crewe Hall", "display": "Crewe Hall, Cheshire East", "lat": 53.07, "lon": -2.39}]}

def test_choices_round_trip_and_replies():
    ch = parse_choices("Do you mean Crewe?\n[map shown]\n" + choices_marker(EV))
    assert ch["mode"] == "route" and len(ch["options"]) == 2
    for reply, idx in [("yes", 0), ("Yes please", 0), ("yeah that one", 0), ("the first one", 0), ("1", 0),
                       ("the second one", 1), ("2", 1), ("number 2", 1), ("crewe hall", 1)]:
        assert choice_from_reply(reply, ch) == idx, reply
    for reply in ["no, Lime Street", "neither", "how far is Leeds?"]:
        assert choice_from_reply(reply, ch) is None, reply
    assert parse_choices("plain answer") is None

@pytest.mark.asyncio
async def test_yes_to_a_place_question_picks_that_place_without_the_model():
    convo = [{"role": "user", "content": "how to get to crewe station, liverpool?"},
             {"role": "assistant", "content": "Do you mean Crewe?\n[map shown]\n" + choices_marker(EV)}]
    llm = LLM({"map": {"mode": "target", "places": ["Liver Building"]}})
    p = await ensure_map(Plan("", "answer", [], False, None, "?"), "yes", llm, convo, pending_question=True)
    assert p.map["mode"] == "route" and p.map["travel"] == "walk" and p.map["places"] == ["Crewe @53.089,-2.433"]
    assert llm.calls == []

def test_recover_route_phrases():
    assert recover_route_phrases(["Liverpool"], "how to get to crewe station, liverpool?", "route") == ["crewe station, liverpool"]
    assert recover_route_phrases(["Formby"], "how can I get to formby station?", "route") == ["formby station"]
    assert recover_route_phrases(["Lime Street", "Albert Dock"], "directions from Lime Street station to Albert Dock, walking", "route") == ["Lime Street station", "Albert Dock"]
    assert recover_route_phrases(["Albert Dock"], "where is Albert Dock?", "target") == ["Albert Dock"]
    assert recover_route_phrases(["Tokyo"], "flight route from Manchester to Tokyo", "route") == ["Manchester", "Tokyo"]   # named start restored

# ---- a start the user never gave is dropped (model put the destination or "YOUR Location" in from) ----

def test_invented_start_is_dropped_when_user_named_none():
    assert recover_route_phrases(["Crewe station", "Liverpool"], "how to get to crewe station, liverpool?", "route") == ["crewe station, liverpool"]
    assert recover_route_phrases(["YOUR Location", "Formby station"], "how can I get to formby station?", "route") == ["Formby station"]
    assert recover_route_phrases(["Lime Street", "Formby station"], "walk from Lime Street to Formby station", "route") == ["Lime Street", "Formby station"]
    assert recover_route_phrases(["Lime Street", "Formby station"], "starting at Lime Street, how do I get to Formby station?", "route") == ["Lime Street", "Formby station"]

def test_location_words_in_from_mean_the_user():
    for w in ["YOUR Location", "my location", "current location", "here", "me", "my current position"]:
        assert normalize_map({"mode": "route", "from": w, "to": "Formby station"})["places"] == ["Formby station"], w
    assert normalize_map({"mode": "route", "places": ["my location", "Formby station"]})["places"] == ["Formby station"]

# ---- second live run: route wording, corrections after a map ----
from aon.geo import force_route_mode

def test_route_wording_forces_route_mode():
    m = {"mode": "area", "places": ["Crewe station", "Liverpool"], "travel": "drive", "view": "auto", "orbit": None}
    out = force_route_mode(m, "how to get to crewe station, liverpool?")
    assert out["mode"] == "route"
    t = {"mode": "target", "places": ["Formby station"], "travel": "drive", "view": "auto", "orbit": None}
    assert force_route_mode(t, "directions to formby station")["mode"] == "route"
    assert force_route_mode(t, "where is formby station?")["mode"] == "target"
    a = {"mode": "area", "places": ["Yangon", "Seoul"], "travel": "drive", "view": "auto", "orbit": None}
    assert force_route_mode(a, "compare Yangon and Seoul")["mode"] == "area"

def test_corrections_after_a_map_are_map_requests():
    for t in ["no, I meant Lime Street station", "actually Formby", "I mean the Liver Building", "Crewe instead"]:
        assert map_hint(t, map_open=True), t
    assert not map_hint("no, I meant the python script", map_open=True)
    assert not map_hint("no, I meant Lime Street station")          # no map open: ordinary chat

# ---- "to X from Y" order ----

def test_recover_handles_to_before_from():
    g = "how to get to brunswick station from Edgehill station?"
    assert recover_route_phrases(["Edgehill station", "brunswick station"], g, "route") == ["Edgehill station", "brunswick station"]
    assert recover_route_phrases(["Edgehill", "Brunswick"], g, "route") == ["Edgehill station", "brunswick station"]

# ---- "how to get to X from Y": recognised, and a dropped start restored ----

@pytest.mark.parametrize("text", ["how to get to brunswick station from Edgehill station?", "how to go to Formby",
                                  "best way to get to Crosby beach", "how to reach Lime Street station"])
def test_map_hint_catches_how_to_get_to(text):
    assert map_hint(text)

def test_dropped_start_is_restored_from_the_message():
    g = "how to get to brunswick station from Edgehill station?"
    assert recover_route_phrases(["brunswick station"], g, "route") == ["Edgehill station", "brunswick station"]
    assert recover_route_phrases(["Formby station"], "walk from Crosby to Formby station", "route") == ["Crosby", "Formby station"]
    assert recover_route_phrases(["Formby station"], "how can I get to formby station?", "route") == ["Formby station"]
