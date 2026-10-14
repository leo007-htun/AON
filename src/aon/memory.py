from __future__ import annotations
import re
import sqlite3
import time
from pathlib import Path
from aon.tools.base import Tool, ToolResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, content TEXT, tags TEXT,
  source TEXT, confidence REAL DEFAULT 1.0, active INTEGER DEFAULT 1,
  superseded_by INTEGER, created_at REAL, last_used_at REAL);
CREATE TABLE IF NOT EXISTS episode_vectors(
  episode_id INTEGER, model TEXT, vec BLOB, PRIMARY KEY(episode_id, model));
"""


_STOP = set("""a an the i me my mine we our you your he she it its they them their is am are was
were be been do does did to of in on at for from with and or but not no so if then than that this
these those what which who whom whose when where why how can could would should will shall may
might must have has had want wants any some about tell know remember please just""".split())


_SECRET = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|\bsk-[A-Za-z0-9_-]{20,}"
    r"|\bAKIA[0-9A-Z]{16}\b|\bxox[abposr]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\bBearer\s+[A-Za-z0-9._~+/=-]{20,}")


def contains_secret(text) -> bool:
    """Tokens/keys must never land in long-term memory (it is injected into prompts and shown in the UI)."""
    return bool(_SECRET.search(text or ""))


def _tokens(text):
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOP and len(t) > 1]


class EpisodicMemory:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._c = sqlite3.connect(str(self.path))
        self._c.row_factory = sqlite3.Row
        self._c.executescript(_SCHEMA)
        self._c.commit()

    def add(self, content, type="fact", tags="", source="manual", confidence=1.0, supersedes=None):
        content = (content or "").strip()
        if not content or contains_secret(content):
            return -1
        norm = content.lower()
        row = self._c.execute(
            "SELECT id FROM episodes WHERE active=1 AND lower(content)=?", (norm,)).fetchone()
        if row:
            self._c.execute("UPDATE episodes SET created_at=?, confidence=? WHERE id=?",
                            (time.time(), confidence, row["id"]))
            self._c.commit()
            return row["id"]
        cur = self._c.execute(
            "INSERT INTO episodes(type,content,tags,source,confidence,active,created_at)"
            " VALUES(?,?,?,?,?,1,?)", (type, content, tags, source, confidence, time.time()))
        nid = cur.lastrowid
        if supersedes:
            self._c.execute("UPDATE episodes SET active=0, superseded_by=? WHERE id=?",
                            (nid, supersedes))
        self._c.commit()
        return nid

    def _rows(self, sql, args=()):
        return [dict(r) for r in self._c.execute(sql, args)]

    def recent(self, limit=8):
        return self._rows(
            "SELECT * FROM episodes WHERE active=1 ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))

    def search(self, query, limit=8):
        toks = _tokens(query)
        if not toks:
            return []
        rows = self._rows("SELECT * FROM episodes WHERE active=1")

        def score(r):
            hay = (r["content"] + " " + (r["tags"] or "")).lower()
            return sum(1 for t in toks if t in hay)

        rows = [r for r in rows if score(r) > 0]
        rows.sort(key=lambda r: (score(r), r["created_at"]), reverse=True)
        rows = rows[:limit]
        if rows:
            self._c.execute("UPDATE episodes SET last_used_at=? WHERE id IN (%s)" %
                            ",".join("?" * len(rows)),
                            [time.time()] + [r["id"] for r in rows])
            self._c.commit()
        return rows

    def all(self, include_inactive=False):
        sql = "SELECT * FROM episodes" + ("" if include_inactive else " WHERE active=1")
        return self._rows(sql + " ORDER BY created_at DESC, id DESC")

    def get(self, id):
        r = self._c.execute("SELECT * FROM episodes WHERE id=?", (id,)).fetchone()
        return dict(r) if r else None

    def delete(self, id):
        self._c.execute("DELETE FROM episodes WHERE id=?", (id,))
        self._c.execute("DELETE FROM episode_vectors WHERE episode_id=?", (id,))
        self._c.commit()

    def clear(self):
        self._c.execute("DELETE FROM episodes")
        self._c.execute("DELETE FROM episode_vectors")
        self._c.commit()

    def mark_outdated(self, id, superseded_by=None):
        self._c.execute("UPDATE episodes SET active=0, superseded_by=COALESCE(?, superseded_by) WHERE id=?",
                        (superseded_by, id))
        self._c.commit()

    # --- embeddings of episodes (search by meaning lives in memory_semantic.py) ---
    def missing_vectors(self, model, limit=200):
        return self._rows(
            "SELECT e.id,e.content FROM episodes e LEFT JOIN episode_vectors v ON v.episode_id=e.id AND v.model=? "
            "WHERE e.active=1 AND v.episode_id IS NULL ORDER BY e.id LIMIT ?", (model, limit))

    def save_vectors(self, rows):
        """rows: (episode_id, model, vec bytes)"""
        self._c.executemany("INSERT OR REPLACE INTO episode_vectors(episode_id,model,vec) VALUES(?,?,?)", rows)
        self._c.commit()

    def vector_rows(self, model):
        return self._rows("SELECT e.*, v.vec FROM episodes e JOIN episode_vectors v ON v.episode_id=e.id "
                          "WHERE e.active=1 AND v.model=? ORDER BY e.id", (model,))

    def context_for(self, query, recent=6, relevant=6, ranked=None):
        """Memory block for a request: episodes relevant to it first, then the most recent.
        ranked: episodes already found by meaning; keyword matches fill the remaining places."""
        rel = list(ranked or [])[:relevant]
        seen = {r["id"] for r in rel}
        if len(rel) < relevant:
            rel += [r for r in self.search(query, relevant) if r["id"] not in seen][:relevant - len(rel)]
        ids = {r["id"] for r in rel}
        rec = [r for r in self.recent(recent) if r["id"] not in ids]
        line = lambda r: f"- [{r['type']}] {r['content']}"
        if not rel:
            return "\n".join(map(line, rec))
        out = "Relevant to this request:\n" + "\n".join(map(line, rel))
        return out + ("\n\nRecent:\n" + "\n".join(map(line, rec)) if rec else "")

    def as_prompt_text(self, limit=8):
        return "\n".join(f"- [{r['type']}] {r['content']}" for r in self.recent(limit))

    # back-compat shim
    def read(self):
        return self.as_prompt_text()

    def append(self, fact):
        return self.add(fact)


MemoryStore = EpisodicMemory


class RememberTool(Tool):
    name = "remember"
    description = "Save a durable fact/preference/decision to long-term memory (episodic)."
    parameters = {"type": "object", "properties": {
        "content": {"type": "string"},
        "type": {"type": "string", "description": "preference|decision|fact|milestone|action|reference"},
        "tags": {"type": "string"}}, "required": ["content"]}
    mutating = False

    def __init__(self, store):
        self.store = store

    async def run(self, args):
        if contains_secret(args.get("content", "")):
            return ToolResult(ok=True, output="not saved: it contains a secret (token/key). Never store secrets in memory.")
        self.store.add(args["content"], type=args.get("type", "fact"), tags=args.get("tags", ""))
        return ToolResult(ok=True, output="saved to memory. Do not call remember again for this; continue or reply.")
