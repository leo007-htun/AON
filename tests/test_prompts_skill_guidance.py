from aon.prompts import build_messages


def test_create_skill_guidance_present():
    msgs = build_messages("g", [], skill_list=[("creating-skills", "how")])
    sys = msgs[0]["content"]
    assert "read_skill" in sys and "creating-skills" in sys and "write_skill" in sys and "draft_skill" in sys
