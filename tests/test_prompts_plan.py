from aon.prompts import build_messages


def test_plan_hint_injected():
    msgs = build_messages("g", [], plan_hint="Intent: create_skill_from_url\nDo now: draft_skill(url=http://x)")
    sys = msgs[0]["content"]
    assert "plan for this turn" in sys.lower() and "draft_skill" in sys


def test_no_plan_hint_ok():
    sys = build_messages("g", [])[0]["content"]
    assert "plan for this turn" not in sys.lower()
