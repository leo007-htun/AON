from aon.prompts import SYSTEM_PROMPT, build_messages

def test_system_prompt_mentions_key_behaviours():
    s = SYSTEM_PROMPT.lower()
    assert "reflect" in s and "plan" in s and "repeat" in s

def test_build_messages_shape():
    msgs = build_messages("do the thing", history=[{"role": "assistant", "content": "prior"}])
    assert msgs[0]["role"] == "system"
    assert msgs[1] == {"role": "assistant", "content": "prior"}
    assert msgs[-1] == {"role": "user", "content": "do the thing"}
