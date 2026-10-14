"""Every request is kept under the model's context window: estimate, clip, drop oldest first."""
from agent.context import (estimate_tokens, message_tokens, clip_text, fit_history, fit_messages,
                           input_limit)


def _msgs(history, goal="the request", trail=()):
    return [{"role": "system", "content": "sys"}, *history, {"role": "user", "content": goal}, *trail]


def _total(msgs, tools=None):
    return sum(message_tokens(m) for m in msgs) + estimate_tokens(tools or [])


def test_estimate_is_conservative_for_ascii():
    # measured on the live model: 3.6 to 4.6 chars per token; the estimate assumes 3
    assert estimate_tokens("a" * 300) == 100


def test_non_ascii_counts_one_token_per_character():
    assert estimate_tokens("ကခဂ") == 3
    assert estimate_tokens("ab" + "中文") == 1 + 2


def test_image_parts_are_counted():
    m = {"role": "user", "content": [{"type": "text", "text": "a" * 30},
                                     {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 90000}}]}
    n = message_tokens(m)
    assert 1000 <= n <= 3000      # a fixed allowance, not the base64 length


def test_clip_keeps_head_and_tail():
    t = "HEAD" + "x" * 30000 + "TAIL"
    c = clip_text(t, 1000)
    assert c.startswith("HEAD") and c.endswith("TAIL")
    assert estimate_tokens(c) <= 1000 and "cut" in c


def test_clip_leaves_short_text_alone():
    assert clip_text("short", 100) == "short"


def test_input_limit_reserves_room_for_the_reply():
    assert input_limit(32768, 6144) == 26624


def test_fit_history_keeps_newest_turns():
    turns = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn{i} " + "x" * 3000} for i in range(20)]
    kept = fit_history(turns, 5000)
    assert kept[-1]["content"].startswith("turn19")
    assert sum(message_tokens(t) for t in kept) <= 5000
    assert [t["content"][:6] for t in kept] == [t["content"][:6] for t in turns[-len(kept):]]


def test_fit_history_clips_one_huge_turn_instead_of_dropping_everything():
    turns = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "y" * 200000}]
    kept = fit_history(turns, 4000)
    assert len(kept) == 2 and message_tokens(kept[1]) < 4000


def test_fit_messages_leaves_small_requests_unchanged():
    msgs = _msgs([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}])
    out, info = fit_messages(msgs, 2, [], 26624)
    assert out == msgs and info == {"dropped": 0, "clipped": 0}


def test_fit_messages_drops_oldest_history_first():
    hist = [{"role": "user", "content": f"old{i} " + "z" * 9000} for i in range(10)]
    msgs = _msgs(hist, goal="GOAL")
    out, info = fit_messages(msgs, len(hist), [], 12000)
    assert _total(out) <= 12000
    assert out[0]["content"] == "sys" and out[-1]["content"] == "GOAL"
    assert info["dropped"] > 0
    assert "old9" in out[-2]["content"]          # newest history kept


def test_fit_messages_clips_tool_output_but_keeps_call_pairs():
    trail = [{"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function",
              "function": {"name": "read_file", "arguments": "{}"}}]},
             {"role": "tool", "tool_call_id": "1", "content": "F" * 150000},
             {"role": "assistant", "content": "", "tool_calls": [{"id": "2", "type": "function",
              "function": {"name": "read_file", "arguments": "{}"}}]},
             {"role": "tool", "tool_call_id": "2", "content": "G" * 150000}]
    msgs = _msgs([{"role": "user", "content": "earlier"}], trail=trail)
    out, info = fit_messages(msgs, 1, [{"type": "function", "function": {"name": "t"}}], 20000)
    assert _total(out, [{"type": "function", "function": {"name": "t"}}]) <= 20000
    assert [m["role"] for m in out[-4:]] == ["assistant", "tool", "assistant", "tool"]
    assert out[-1]["tool_call_id"] == "2" and out[-2]["tool_calls"][0]["id"] == "2"
    assert message_tokens(out[-1]) > message_tokens(out[-3])   # the latest result keeps more room
    assert info["clipped"] >= 2


def test_fit_messages_fits_even_when_everything_is_huge():
    hist = [{"role": "assistant", "content": "h" * 100000} for _ in range(5)]
    trail = [{"role": "tool", "tool_call_id": "1", "content": "t" * 300000}]
    msgs = _msgs(hist, goal="g" * 120000, trail=trail)
    out, _ = fit_messages(msgs, 5, [], 26624)
    assert _total(out) <= 26624
    assert out[0]["role"] == "system" and any(m["role"] == "tool" for m in out)
