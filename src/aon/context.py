"""Context budget: keep every model request inside the model's context window.

Ollama rejects a request longer than the window (HTTP 400 "exceeds the available context size"),
and the window also has to hold the model's thinking and its answer. Token counts are estimated
from characters: measured on the live model, English, code, tool schemas and real chat turns run
3.6 to 4.6 characters per token, so 3 per token leaves a margin; non-ASCII characters (Burmese,
Chinese, emoji) can cost a token or more each and are counted as one.
"""
from __future__ import annotations
import json
import math

CHARS_PER_TOKEN = 3
IMAGE_TOKENS = 1500          # an attached image, whatever its base64 length
MESSAGE_OVERHEAD = 4         # role markers around each message


def estimate_tokens(x) -> int:
    if x is None:
        return 0
    if not isinstance(x, str):
        x = json.dumps(x, ensure_ascii=False)
    ascii_n = sum(1 for ch in x if ord(ch) < 128)
    return math.ceil(ascii_n / CHARS_PER_TOKEN) + (len(x) - ascii_n)


def _content_tokens(content) -> int:
    if isinstance(content, list):
        n = 0
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                n += IMAGE_TOKENS
            elif isinstance(part, dict):
                n += estimate_tokens(part.get("text", ""))
            else:
                n += estimate_tokens(part)
        return n
    return estimate_tokens(content or "")


def message_tokens(m: dict) -> int:
    n = MESSAGE_OVERHEAD + _content_tokens(m.get("content"))
    if m.get("tool_calls"):
        n += estimate_tokens(m["tool_calls"])
    return n


def input_limit(window: int, reply_reserve: int) -> int:
    """Tokens a request may use: the window minus room for thinking and the answer."""
    return max(1024, window - reply_reserve)


def clip_text(text: str, max_tokens: int) -> str:
    """Shorten text to about max_tokens, keeping its start and end."""
    text = text or ""
    if estimate_tokens(text) <= max_tokens:
        return text
    keep = max(0, max_tokens * CHARS_PER_TOKEN - 80)
    while True:
        head, tail = text[: keep * 2 // 3], (text[-(keep // 3):] if keep // 3 else "")
        cut = len(text) - len(head) - len(tail)
        out = f"{head}\n…[{cut} characters cut to fit the context]…\n{tail}"
        if estimate_tokens(out) <= max_tokens or keep == 0:
            return out if estimate_tokens(out) <= max_tokens else text[:max(0, max_tokens)]
        keep = int(keep * 0.8)


def _clip_message(m: dict, max_tokens: int) -> dict:
    budget = max(0, max_tokens - MESSAGE_OVERHEAD - (estimate_tokens(m["tool_calls"]) if m.get("tool_calls") else 0))
    c = m.get("content")
    if isinstance(c, list):
        fixed = sum(IMAGE_TOKENS for p in c if isinstance(p, dict) and p.get("type") == "image_url")
        texts = [p for p in c if isinstance(p, dict) and p.get("type") == "text"]
        each = max(0, (budget - fixed) // max(1, len(texts)))
        parts = [dict(p, text=clip_text(p.get("text", ""), each)) if p in texts else p for p in c]
        return dict(m, content=parts)
    return dict(m, content=clip_text(c or "", budget))


def fit_history(turns: list[dict], budget: int) -> list[dict]:
    """The newest turns that fit in budget tokens, oldest dropped first; no single turn may take
    more than half the budget (a long paste is clipped rather than pushing everything else out)."""
    cap = max(200, budget // 2)
    kept, used = [], 0
    for t in reversed(turns or []):
        if message_tokens(t) > cap:
            t = _clip_message(t, cap)
        n = message_tokens(t)
        if used + n > budget:
            break
        kept.append(t)
        used += n
    return list(reversed(kept))


def fit_messages(messages: list[dict], n_history: int, tools, limit: int):
    """Fit [system, *history, request, *this run's tool calls/results] into limit tokens.

    In order, until it fits: clip very long history turns, drop the oldest history (keeping the
    last exchange), clip older tool results, drop the rest of the history, then clip the latest
    tool result, the request and finally the system prompt. Tool calls and their results are
    never removed, only shortened, so each call keeps its result."""
    msgs = list(messages)
    info = {"dropped": 0, "clipped": 0}
    fixed = estimate_tokens(tools) if tools else 0

    def total():
        return fixed + sum(message_tokens(m) for m in msgs)

    if total() <= limit:
        return messages, info
    hist_lo, hist_hi = 1, 1 + n_history          # msgs[hist_lo:hist_hi] is the history

    for i in range(hist_lo, hist_hi):
        if message_tokens(msgs[i]) > 2000:
            msgs[i] = _clip_message(msgs[i], 2000); info["clipped"] += 1

    def drop_history(keep):
        nonlocal hist_hi
        while total() > limit and hist_hi - hist_lo > keep:
            del msgs[hist_lo]; hist_hi -= 1; info["dropped"] += 1

    drop_history(2)
    tool_idx = [i for i in range(hist_hi + 1, len(msgs)) if msgs[i].get("role") == "tool"]
    if total() > limit:
        for i in tool_idx[:-1]:
            if message_tokens(msgs[i]) > 500:
                msgs[i] = _clip_message(msgs[i], 500); info["clipped"] += 1
    drop_history(0)
    tool_idx = [i for i in range(hist_hi + 1, len(msgs)) if msgs[i].get("role") == "tool"]
    for i in ([tool_idx[-1]] if tool_idx else []) + [hist_hi, 0]:
        over = total() - limit
        if over <= 0:
            break
        floor = 1000 if i == 0 else 300
        target = max(floor, message_tokens(msgs[i]) - over)
        if target < message_tokens(msgs[i]):
            msgs[i] = _clip_message(msgs[i], target); info["clipped"] += 1
    return msgs, info


async def fit_exact(messages: list[dict], n_history: int, tools, limit: int, counter, tries: int = 3):
    """fit_messages, checked against the server's exact count when it has one.

    Dense text (logs, IPs, numbers) runs under 3 characters per token, so the estimate can pass
    a request the server then rejects. When the real count is over the limit, fit again to a
    smaller estimated budget, scaled by how far the estimate was off."""
    fitted, _ = fit_messages(messages, n_history, tools, limit)
    budget = limit
    for attempt in range(tries + 1):
        try:
            real = await counter(fitted, tools)
        except Exception:
            real = None
        if not real or real <= limit or attempt == tries:
            return fitted
        est = sum(message_tokens(m) for m in fitted) + (estimate_tokens(tools) if tools else 0)
        budget = max(1, int(min(budget, est) * limit / real * 0.97))
        fitted, _ = fit_messages(messages, n_history, tools, budget)
    return fitted
