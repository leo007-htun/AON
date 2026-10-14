from __future__ import annotations
import json
import re
from dataclasses import dataclass
from agent.geo import (normalize_map, map_hint, merge_qualifiers, expand_place_types, parse_choices,
                       choice_from_reply, recover_route_phrases, force_route_mode)


@dataclass
class Plan:
    understanding: str
    intent: str
    plan: list
    needs_clarification: bool
    question: str | None
    direct_answer: str | None
    map: dict | None = None


_SYS = (
    "You are the planner for AON, a gated dev agent. Read the user's request and the agent's "
    "capabilities, then decide what they want and the actions to take. Return ONLY a JSON object: "
    "{\"understanding\":\"...\",\"intent\":\"create_skill_from_url|create_skill|learn_url|run_evals|"
    "recall|remember|capabilities|shell_task|answer\",\"plan\":[{\"tool\":\"<name>\",\"args\":{...}}],"
    "\"needs_clarification\":false,\"question\":null,\"direct_answer\":null}. "
    "If a URL should become a skill, plan draft_skill. If it's a plain question you can answer "
    "without tools, put the answer in direct_answer with intent 'answer'. If anything is unclear, "
    "ambiguous, underspecified, or you are not confident of the intent or the required args (e.g. a "
    "missing URL, skill name, or target), set needs_clarification=true and ask ONE focused follow-up "
    "question — do NOT guess a plan. Only produce a plan when the intent and its args are concrete. "
    "For install/run/setup tasks, make the FIRST step a verification (e.g. `which <bin>` or listing "
    "the workspace) before acting, then the remaining concrete steps in order. "
    "Never invent tools not in the provided list. "
    "Questions about the user or earlier chat (their preferences, plans, what they said) are "
    "intent 'recall': answer from the recent conversation and memory; if it is not there, plan "
    "search_memory (and search_conversation) with the key words. Never ask the user to repeat "
    "something they may already have told you, and never answer 'I don't know' without searching. "
    "direct_answer is ONLY for questions you can answer from knowledge. Never claim in it that "
    "anything was added, installed, saved, sent or configured: that requires a plan with tools. "
    "If the user supplies something a task needs (a token, URL, name), plan the tool call that uses it. "
    "A plain statement about the user (e.g. 'My favorite X is Y', 'I prefer A over B') is intent "
    "'remember': plan remember with that fact and acknowledge it briefly; do not ask what to do with it. "
    "Also decide whether a map helps and add \"map\": null or {\"mode\":\"target|route|area|update\","
    "\"places\":[\"...\"],\"travel\":\"drive|walk|cycle|flight\",\"view\":\"auto|2d|3d|globe\",\"orbit\":null}. "
    "Use a map whenever the request is about places: where something is, directions, routes or distance "
    "between places, what is near somewhere, comparing locations, travel. mode 'target' = one specific place; "
    "'route' = getting from A to B with places [origin, destination] (if the origin is not given, places = "
    "[destination] only; never invent the origin); 'area' = countries, regions or several places; 'update' = "
    "change the map already shown (e.g. 'show it in 3D', 'switch to globe', 'stop orbiting', 'walk instead') "
    "with places []. Copy place names as the user wrote them, adding the city/country if the conversation "
    "makes it clear. Set view/orbit only if the user asks for them (3D, globe, orbit/spin/rotate). "
    "map is null for code, files and anything not about places. Examples: 'Where is Albert Dock?' -> "
    "{\"mode\":\"target\",\"places\":[\"Albert Dock\"]}; 'How do I get to Albert Dock?' -> "
    "{\"mode\":\"route\",\"places\":[\"Albert Dock\"]}; 'directions from Lime Street to the Liver Building' -> "
    "{\"mode\":\"route\",\"places\":[\"Lime Street\",\"Liver Building\"]}; 'switch to globe and spin it' -> "
    "{\"mode\":\"update\",\"places\":[],\"view\":\"globe\",\"orbit\":true}; 'stop rotating' -> "
    "{\"mode\":\"update\",\"places\":[],\"orbit\":false}. Include map even when you also give direct_answer.")


def _json_obj(text):
    s = text.find("{"); e = text.rfind("}")
    if s != -1 and e > s:
        return json.loads(text[s:e + 1])
    raise ValueError("no json object")


async def deliberate(goal, llm, *, tools_summary="", skills_summary="", memory_text="", valid_tools=None, conversation=None):
    try:
        convo_txt = ""
        if conversation:
            convo_txt = ("Recent conversation:\n"
                         + "\n".join(f"{t['role']}: {t['content']}" for t in conversation) + "\n\n")
        ctx = (convo_txt + f"User request:\n{goal}\n\nTools:\n{tools_summary}\n\n"
               f"Skills:\n{skills_summary}\n\nMemory:\n{memory_text}")
        res = await llm.chat([{"role": "system", "content": _SYS},
                              {"role": "user", "content": ctx}])
        d = _json_obj(res.content or "")
        actions = []
        for a in (d.get("plan") or []):
            if isinstance(a, dict) and a.get("tool"):
                if valid_tools is None or a["tool"] in valid_tools:
                    actions.append({"tool": a["tool"], "args": a.get("args") or {}})
        return Plan(
            understanding=str(d.get("understanding", "")),
            intent=str(d.get("intent", "task")),
            plan=actions,
            needs_clarification=bool(d.get("needs_clarification")),
            question=(d.get("question") or None),
            direct_answer=(d.get("direct_answer") or None),
            map=normalize_map(d.get("map")))
    except Exception:
        return Plan("", "task", [], False, None, None)


_MAP_SYS = (
    "Extract the map request from the user's message. Return ONLY JSON: {\"map\": null} or {\"map\": "
    "{\"mode\":\"target|route|area|update\",\"places\":[...],\"from\":null,\"to\":null,"
    "\"travel\":\"drive|walk|cycle|flight\",\"view\":\"auto|2d|3d|globe\",\"orbit\":null}}. "
    "target = one place (in places); area = several places, countries or regions (in places); "
    "route = getting somewhere: put the destination in \"to\" and the start in \"from\", or \"from\": null when "
    "the user names no start (null means the user's current location; never invent a start); "
    "update = change the map already shown (view, orbit, travel) with places []. Copy place names as written, "
    "keeping words like station, airport or park. If the assistant just asked which place the user means "
    "and the user answers (\"yes\", \"the first one\", \"no, Lime Street\"), use the place they chose, with "
    "its town from the question. Examples: 'Where is Albert Dock?' -> {\"map\":{\"mode\":\"target\","
    "\"places\":[\"Albert Dock\"]}}; 'How do I get to Albert Dock?' -> {\"map\":{\"mode\":\"route\","
    "\"from\":null,\"to\":\"Albert Dock\"}}; 'walk from Lime Street to Formby station' -> {\"map\":{\"mode\":"
    "\"route\",\"from\":\"Lime Street\",\"to\":\"Formby station\",\"travel\":\"walk\"}}; "
    "'show it in 3D' -> {\"map\":{\"mode\":\"update\",\"places\":[],"
    "\"view\":\"3d\"}}; 'spin it' -> {\"map\":{\"mode\":\"update\",\"places\":[],\"orbit\":true}}; "
    "'Where is the Liver Building in Liverpool?' -> {\"map\":{\"mode\":\"target\",\"places\":[\"Liver Building, "
    "Liverpool\"]}} (a city after 'in' narrows the place; it is not a second place); "
    "'fix my code' -> {\"map\":null}.")


def pending_place_question(conversation) -> bool:
    """True when AON's last reply asked which place the user means (a map was shown with a question)."""
    for t in reversed(conversation or []):
        if t.get("role") == "assistant":
            c = t.get("content") or ""
            visible = re.sub(r"\[choices \{.*\}\]", "", c, flags=re.S).replace("[map shown]", "").strip()
            return "[map shown]" in c and visible.endswith("?")
    return False


async def ensure_map(plan, goal, llm, conversation=None, map_open=False, pending_question=False, allow_extract=True):
    """The planner sometimes leaves `map` empty; when the wording looks map-related, ask once, narrowly.
    A reply to AON's own "which place do you mean?" always goes to the narrow extractor, which sees it."""
    if plan is None:
        return plan
    if pending_question:
        # "yes" / "the second one" / a listed name: take that exact candidate, no model guess
        last = next((t.get("content") or "" for t in reversed(conversation or []) if t.get("role") == "assistant"), "")
        ch = parse_choices(last)
        idx = choice_from_reply(goal, ch) if ch else None
        if idx is not None:
            o = ch["options"][idx]
            places = ([ch["from"]] if ch.get("from") else []) + [f"{o['name']} @{o['lat']},{o['lon']}"]
            plan.map = normalize_map({"mode": ch["mode"] if ch["mode"] in ("route", "target") else "target",
                                      "places": places, "travel": ch.get("travel", "drive")})
            return plan
    if allow_extract and (pending_question or (not plan.map and map_hint(goal, map_open))):
        if pending_question:
            plan.map = None
        await _extract_map(plan, goal, llm, conversation)
    if plan.map and plan.map.get("places"):
        plan.map = force_route_mode(plan.map, goal)
        places = expand_place_types(merge_qualifiers(plan.map["places"], goal), goal)
        places = recover_route_phrases(places, goal, plan.map["mode"])
        mode = "target" if plan.map["mode"] == "area" and len(places) == 1 else plan.map["mode"]
        plan.map = {**plan.map, "places": places, "mode": mode}
    return plan


async def _extract_map(plan, goal, llm, conversation):
    try:
        ctx = "".join(f"{t['role']}: {t['content']}\n" for t in (conversation or [])[-4:])
        res = await llm.chat([{"role": "system", "content": _MAP_SYS},
                              {"role": "user", "content": (ctx and "Recent conversation:\n" + ctx + "\n") + "Message: " + goal}])
        d = _json_obj(res.content or "")
        plan.map = normalize_map(d.get("map") if "map" in d else d)
    except Exception:
        pass
    return plan


def render_step(a) -> str:
    tool = (a or {}).get("tool", "?")
    args = (a or {}).get("args") or {}
    argstr = ", ".join(f"{k}={v}" for k, v in args.items())
    return f"{tool}({argstr})"


def plan_progress(steps, done, last_error, understanding="", intent=""):
    if not steps:
        return ""
    lines = []
    if understanding:
        lines.append("Understanding: " + understanding)
    lines.append("Your plan (work the current step; adapt/re-plan if a step fails):")
    for i, s in enumerate(steps):
        mark = "x" if i < done else (">" if i == done else " ")
        lines.append(f"[{mark}] {s}")
    if last_error:
        lines.append(f"⚠ last step errored: {last_error} (re-plan if needed)")
    return "\n".join(lines)


def render_plan_hint(plan) -> str:
    if plan is None or getattr(plan, "needs_clarification", False):
        return ""
    if getattr(plan, "intent", "") == "answer":
        return ""
    actions = getattr(plan, "plan", None) or []
    understanding = getattr(plan, "understanding", "")
    if not actions and not understanding:
        return ""
    lines = []
    if understanding:
        lines.append("Understanding: " + understanding)
    if getattr(plan, "intent", ""):
        lines.append("Intent: " + plan.intent)
    if actions:
        acts = "; ".join(
            "{}({})".format(a.get("tool"),
                            ", ".join(f"{k}={v}" for k, v in (a.get("args") or {}).items()))
            for a in actions)
        lines.append("Do now: " + acts + ". Execute this plan; ask only if a step fails.")
    return "\n".join(lines)
