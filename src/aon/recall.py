"""Recall by meaning: chat turns are embedded (nomic-embed-text via Ollama) and stored in the
database; for a new message, the older turns closest in meaning are found and sent as
background. Vectors are unit length, so cosine similarity is a dot product. Without numpy,
plain Python is fast enough for a chat's few hundred turns.
"""
from __future__ import annotations
from array import array
import math
import re
import time
import httpx
from aon.context import clip_text, estimate_tokens

DOC = "search_document: "     # nomic-embed-text expects a task prefix
QUERY = "search_query: "
MIN_SCORE = 0.55              # measured on real chats: related 0.60-0.82, unrelated up to 0.52
MIN_CHARS = 12                # "hi" / "ok" are never worth recalling
EMBED_CHARS = 6000            # text sent to the embedder per turn
TURN_TOKENS = 600             # a recalled turn as it is sent to the model


class Embedder:
    def __init__(self, base_url: str, model: str, transport=None, timeout: float = 60):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._transport = transport
        self._timeout = timeout

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
            r = await c.post(f"{self.base_url}/embeddings", json={"model": self.model, "input": list(texts)})
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        return [_unit(d["embedding"]) for d in data]


def _unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _pack(v) -> bytes:
    return array("f", v).tobytes()


def _unpack(b) -> array:
    a = array("f"); a.frombytes(b); return a


async def index_turns(db, embedder, convo, limit=64) -> int:
    """Embed this chat's turns that have no vector yet (at most `limit`). Returns how many."""
    rows = db.turns_missing_vectors(convo, embedder.model, limit)
    if not rows:
        return 0
    try:
        vecs = []
        for i in range(0, len(rows), 16):
            batch = rows[i:i + 16]
            vecs += await embedder.embed([DOC + (r["content"] or "")[:EMBED_CHARS] for r in batch])
    except Exception:
        return 0
    db.save_vectors([(r["id"], embedder.model, convo, _pack(_unit(v))) for r, v in zip(rows, vecs)])
    return len(rows)


# AON's own status lines, not content: they matched unrelated questions in testing
_NOISE = ("Stopped:", "The model returned an empty reply", "(stopped by the user)", "The model (",
          "Could not reach the model server", "The run failed:")


def _usable(text) -> bool:
    t = (text or "").strip()
    return len(t) >= MIN_CHARS and not t.startswith(_NOISE)


async def _query_vector(embedder, query):
    if not (query or "").strip():
        return None
    try:
        return _unit((await embedder.embed([QUERY + query[:EMBED_CHARS]]))[0])
    except Exception:
        return None


def _score(q, rows, min_score):
    scored = []
    for r in rows:
        v = _unpack(r["vec"])
        if len(v) == len(q):
            s = sum(a * b for a, b in zip(q, v))
            if s >= min_score:
                scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    return scored


def exact_terms(text) -> list[str]:
    """Strings embeddings match poorly: anything with a digit (IPs, ports, versions, amounts),
    dotted/hyphenated codes and long ALLCAPS names (HALCYON). Short acronyms (API, HTTP) are too
    common to single anything out."""
    out = []
    for tok in re.findall(r"[\w][\w.\-:/@]*[\w]", text or ""):
        if any(ch.isdigit() for ch in tok) or (tok.isupper() and len(tok) >= 5) or re.search(r"\w[.\-_]\w", tok):
            out.append(tok)
    return out


def _with_exact(scored, rows, query):
    """Turns containing every exact string in the query go first (score 1.0), then the rest."""
    terms = [t.lower() for t in exact_terms(query)]
    if not terms:
        return scored
    exact = [r for r in rows if all(t in (r["content"] or "").lower() for t in terms)]
    ids = {r["id"] for r in exact}
    return [(1.0, r) for r in reversed(exact)] + [(s, r) for s, r in scored if r["id"] not in ids]


def _gather(db, scored, convo_of, exclude_ids, budget, k):
    """Best matches within budget tokens. A match brings its other half: the answer to a matched
    question ("what is my fav color" -> "Teal", too short to match by itself), or the question a
    matched answer replied to."""
    hits, used, taken = [], 0, set()
    for s, r in scored[:k]:
        if r["id"] in taken:
            continue
        group = [(r, s)]
        p = db.neighbour_turn(convo_of(r), r["id"], after=r["role"] == "user")
        if (p and p["role"] != r["role"] and p["id"] not in exclude_ids and p["id"] not in taken
                and not (p["content"] or "").strip().startswith(_NOISE)):
            group.append((p, None))
        # the best match may use the whole budget (a long answer clipped in the middle loses the
        # part that mattered, and the model fills the gap); later matches are capped
        caps = [TURN_TOKENS] * len(group)
        if not hits:
            if len(group) == 1:
                caps = [budget - used]
            else:
                # the shorter half (usually the question) keeps up to 200 tokens, the longer the rest
                sizes = [estimate_tokens(t["content"]) for t, _ in group]
                short = 0 if sizes[0] <= sizes[1] else 1
                caps[short] = min(sizes[short], 200)
                caps[1 - short] = budget - used - caps[short]
        for (t, sc), cap in zip(group, caps):
            text = clip_text(t["content"], min(cap, budget - used))
            n = estimate_tokens(text)
            if n == 0 or used + n > budget:
                break
            hits.append({"id": t["id"], "role": t["role"], "content": text, "convo": convo_of(t),
                         "created_at": t.get("created_at") or r.get("created_at"),
                         "score": None if sc is None else round(sc, 3)})
            taken.add(t["id"]); used += n
        if used >= budget:
            break
    return sorted(hits, key=lambda h: h["id"])


async def recall(db, embedder, convo, query, exclude_ids, budget=1500, k=6, min_score=MIN_SCORE):
    """This chat's turns closest in meaning to query, not in exclude_ids, within budget tokens,
    in chat order. Empty when nothing qualifies or the embedder fails."""
    rows = [r for r in db.vector_rows(convo, embedder.model)
            if r["id"] not in exclude_ids and _usable(r["content"])]
    if not rows:
        return []
    q = await _query_vector(embedder, query)
    scored = _score(q, rows, min_score) if q is not None else []
    return _gather(db, _with_exact(scored, rows, query), lambda r: convo, exclude_ids, budget, k)


async def recall_background(db, embedder, convo, query, exclude_ids, budget=1500) -> str:
    hits = await recall(db, embedder, convo, query, exclude_ids, budget=budget)
    if not hits:
        return ""
    return ("Earlier messages from this chat that may be relevant (found by meaning; older than the "
            "recent turns):\n" + "\n".join(f"- {h['role']}: {h['content']}" for h in hits))


# --- across all chats ---
CROSS_MIN = 0.70     # measured over 37 real chats: answered elsewhere 0.68-0.88, unrelated 0.52-0.67


async def index_all(db, embedder, limit=64) -> int:
    """Embed turns of any chat that have no vector yet, newest first (at most `limit`)."""
    rows = db.turns_missing_vectors_all(embedder.model, limit)
    if not rows:
        return 0
    try:
        vecs = []
        for i in range(0, len(rows), 16):
            vecs += await embedder.embed([DOC + (r["content"] or "")[:EMBED_CHARS] for r in rows[i:i + 16]])
    except Exception:
        return 0
    db.save_vectors([(r["id"], embedder.model, r["convo"], _pack(_unit(v))) for r, v in zip(rows, vecs)])
    return len(rows)


async def backfill(db, embedder, batch=256) -> int:
    """Index every turn that has no vector yet (old chats included). Returns how many."""
    total = 0
    while True:
        n = await index_all(db, embedder, limit=batch)
        total += n
        if n < batch:
            return total


async def recall_other_chats(db, embedder, convo, query, budget=1000, k=4, min_score=CROSS_MIN):
    """Turns from the user's other chats closest in meaning to query (this chat is left to
    recall()). Empty when nothing reaches min_score or the embedder fails."""
    rows = [r for r in db.other_vector_rows(embedder.model, convo) if _usable(r["content"])]
    if not rows:
        return []
    q = await _query_vector(embedder, query)
    if q is None:
        return []
    return _gather(db, _score(q, rows, min_score), lambda r: r["convo"], set(), budget, k)


async def other_chats_background(db, embedder, convo, query, budget=1000) -> str:
    hits = await recall_other_chats(db, embedder, convo, query, budget=budget)
    if not hits:
        return ""
    day = lambda h: time.strftime("%Y-%m-%d", time.localtime(h["created_at"])) if h.get("created_at") else "?"
    return ("From the user's other chats (found by meaning; may be out of date, the current chat wins "
            "if they disagree):\n" + "\n".join(f"- [{day(h)}] {h['role']}: {h['content']}" for h in hits))
