"""Rolling chat summary, kept in the database.

Only the most recent turns are sent with each request. Turns older than that are folded, a
few at a time, into a short summary stored per chat (chat_summaries), and the summary is sent
as background instead. It is updated after a reply, by the chat's own model.
"""
from __future__ import annotations
import re
from aon.context import clip_text

KEEP = 10            # the newest turns stay out of the summary (they are sent as they are)
MIN_NEW = 6          # fold older turns in once at least this many are waiting
TURN_CHARS = 2000    # each turn as the summariser sees it
CHUNK_CHARS = 24000  # turns per summariser call (about 8K tokens)
SUMMARY_TOKENS = 800

_SYSTEM = ("You keep a running summary of a chat between a user and the assistant AON. Merge the new "
           "messages into the current summary. Keep facts about the user, decisions, names, numbers, file "
           "paths, URLs, commands, results and open tasks or questions. Drop greetings and small talk. "
           "Write short plain-text bullet points, at most 250 words in total. Reply with the summary only.")


def _clean(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    return clip_text(text, SUMMARY_TOKENS)


async def _fold(llm, summary: str, turns: list[dict]) -> str:
    convo = "\n".join(f"{t['role']}: {clip_text(t['content'], TURN_CHARS // 3)}" for t in turns)
    res = await llm.chat([{"role": "system", "content": _SYSTEM},
                          {"role": "user", "content": f"Current summary:\n{summary or '(none yet)'}\n\n"
                                                      f"New messages:\n{convo}"}])
    return _clean(res.content)


async def update_summary(db, llm, convo, keep=KEEP, min_new=MIN_NEW) -> bool:
    """Fold turns older than the newest `keep` into the chat's summary. True if it changed."""
    prev = db.get_summary(convo) or {"text": "", "upto_id": 0}
    rows = db.turn_rows(convo, prev["upto_id"])
    older = rows[:-keep] if len(rows) > keep else []
    if len(older) < min_new:
        return False
    text, changed, i = prev["text"], False, 0
    while i < len(older):
        chunk, size = [], 0
        while i < len(older):
            n = min(len(older[i]["content"]), TURN_CHARS) + 20
            if chunk and size + n > CHUNK_CHARS:
                break
            chunk.append(older[i]); size += n; i += 1
        try:
            new = await _fold(llm, text, chunk)
        except Exception:
            return changed
        if not new:
            return changed
        text = new
        db.set_summary(convo, text, chunk[-1]["id"])
        changed = True
    return changed


OUTLINE_TOKENS = 500
OUTLINE_CHARS = 110     # each message, one line


def outline_background(db, convo, exclude_ids, budget=OUTLINE_TOKENS) -> str:
    """The user's messages in order, one short line each. The summary and recall keep content but
    not order, so "what did I ask first?" was answered wrong; this keeps the order. Built only once
    some messages are no longer sent in full (exclude_ids are the ones that are), and then it lists
    them all: an outline that stopped where the recent turns begin was miscounted ("the third one"
    answered with the fifth). With too many, the first ones and the most recent ones are kept."""
    msgs = [t for t in db.turn_rows(convo) if t["role"] == "user"]
    if all(t["id"] in exclude_ids for t in msgs):
        return ""
    def line(i, t):
        text = re.sub(r"\s+", " ", (t["content"] or "").split("[Attached files", 1)[0]).strip()
        return f"{i}. {text[:OUTLINE_CHARS]}{'…' if len(text) > OUTLINE_CHARS else ''}"
    lines = [line(i + 1, t) for i, t in enumerate(msgs)]
    cost = lambda ls: sum(len(l) // 3 + 2 for l in ls)
    if cost(lines) > budget:
        head, tail = lines[:5], lines[5:]
        while tail and cost(head + ["…"] + tail) > budget:
            tail.pop(0)
        lines = head + ["…"] + tail
    return ("Every message the user has sent in this chat so far, numbered oldest first (one line "
            "each; the current message is not included). Use these numbers for questions like "
            "\"what did I ask first / third / before X\":\n" + "\n".join(lines))


def summary_background(db, convo) -> str:
    s = db.get_summary(convo)
    if not s or not (s["text"] or "").strip():
        return ""
    return "Summary of earlier messages in this chat (older than the recent turns):\n" + s["text"].strip()
