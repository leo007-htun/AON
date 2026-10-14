"""Memory facts by meaning.

Facts (episodes) get nomic-embed-text vectors in episodes.db. For a request, the closest facts
are put in the memory block, ahead of keyword matches. Measured on a 24-question test set
phrased unlike the facts: keyword search found the right fact for 12, embeddings ranked it
first for 21.

Close pairs of facts are ambiguous by score alone (an update such as Liverpool -> Manchester
scored 0.82, two true facts such as pizza / pasta 0.87), so a pair at CANDIDATE or above is
put to the model: SAME, REPLACES or DIFFERENT.
"""
from __future__ import annotations
import re
from aon.recall import DOC, QUERY, _pack, _unpack, _unit

MIN_SCORE = 0.5      # right facts scored 0.46-0.83 and wrong ones up to 0.69, so this is a floor and
                     # the top few are used; a stray extra fact in the memory block costs little
CANDIDATE = 0.80     # pairs below this were never the same fact in testing

_JUDGE = ("You compare two facts from an assistant's long-term memory about its user. Reply with one "
          "word: SAME if they state the same thing, REPLACES if the new fact updates or contradicts the "
          "old one (same subject, new value), DIFFERENT if both can be true at once.")


async def index_episodes(mem, embedder, limit=200) -> int:
    rows = mem.missing_vectors(embedder.model, limit)
    if not rows:
        return 0
    try:
        vecs = []
        for i in range(0, len(rows), 32):
            vecs += await embedder.embed([DOC + r["content"] for r in rows[i:i + 32]])
    except Exception:
        return 0
    mem.save_vectors([(r["id"], embedder.model, _pack(_unit(v))) for r, v in zip(rows, vecs)])
    return len(rows)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


async def search_episodes(mem, embedder, query, limit=6, min_score=MIN_SCORE) -> list[dict]:
    """Active facts closest in meaning to query, best first. Empty if the embedder fails."""
    if not (query or "").strip():
        return []
    await index_episodes(mem, embedder)
    rows = mem.vector_rows(embedder.model)
    if not rows:
        return []
    try:
        q = _unit((await embedder.embed([QUERY + query[:6000]]))[0])
    except Exception:
        return []
    scored = []
    for r in rows:
        v = _unpack(r.pop("vec"))
        if len(v) == len(q):
            s = _dot(q, v)
            if s >= min_score:
                scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:limit]]


class SemanticMemory:
    """EpisodicMemory plus search by meaning. prepare(query) runs the (async) search once per
    request; context_for(query) then uses it, or falls back to keyword search."""

    def __init__(self, mem, embedder):
        self._mem = mem
        self.embedder = embedder
        self._ranked = (None, None)

    def __getattr__(self, name):
        return getattr(self._mem, name)

    async def prepare(self, query):
        try:
            self._ranked = (query, await search_episodes(self._mem, self.embedder, query))
        except Exception:
            self._ranked = (None, None)

    def context_for(self, query, recent=6, relevant=6):
        q, rows = self._ranked
        return self._mem.context_for(query, recent, relevant, ranked=rows if q == query else None)


def _verdict(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip().upper()
    for word in ("REPLACES", "SAME", "DIFFERENT"):
        if text.startswith(word):
            return word
    return "DIFFERENT"


async def consolidate(mem, embedder, llm, candidate=CANDIDATE) -> list[tuple]:
    """Embed new facts; for each, the closest older fact at `candidate` or above is judged by the
    model. SAME: the new copy is retired. REPLACES: the old fact is retired (superseded_by the new).
    Returns the actions taken as (verdict, kept_id, retired_id)."""
    new = mem.missing_vectors(embedder.model)
    if not new:
        return []
    try:
        vecs = await embedder.embed([DOC + r["content"] for r in new])
    except Exception:
        return []
    actions = []
    for r, v in zip(new, vecs):
        v = _unit(v)
        best = None
        for o in mem.vector_rows(embedder.model):
            if o["id"] == r["id"]:
                continue
            ov = _unpack(o["vec"])
            if len(ov) == len(v):
                s = _dot(v, ov)
                if s >= candidate and (best is None or s > best[0]):
                    best = (s, o)
        mem.save_vectors([(r["id"], embedder.model, _pack(v))])
        if best is None:
            continue
        old = best[1]
        try:
            res = await llm.chat([{"role": "system", "content": _JUDGE},
                                  {"role": "user", "content": f"Old fact: {old['content']}\nNew fact: {r['content']}"}])
        except Exception:
            continue
        verdict = _verdict(res.content)
        if verdict == "SAME":
            mem.mark_outdated(r["id"], superseded_by=old["id"]); actions.append(("SAME", old["id"], r["id"]))
        elif verdict == "REPLACES":
            mem.mark_outdated(old["id"], superseded_by=r["id"]); actions.append(("REPLACES", r["id"], old["id"]))
    return actions
