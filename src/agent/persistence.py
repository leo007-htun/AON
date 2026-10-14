import json, sqlite3, time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, workdir TEXT, model TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, role TEXT, content TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS actions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, tool TEXT,
  proposed_args TEXT, decision TEXT, final_args TEXT,
  stdout TEXT, stderr TEXT, exit_code INTEGER, created_at REAL);
CREATE TABLE IF NOT EXISTS conversations(
  id INTEGER PRIMARY KEY AUTOINCREMENT, convo TEXT, role TEXT, content TEXT, created_at REAL);
CREATE INDEX IF NOT EXISTS idx_convo ON conversations(convo, id);
CREATE TABLE IF NOT EXISTS pending_links(
  convo TEXT PRIMARY KEY, data TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS chat_summaries(
  convo TEXT PRIMARY KEY, upto_id INTEGER, text TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS turn_vectors(
  turn_id INTEGER, model TEXT, convo TEXT, vec BLOB, PRIMARY KEY(turn_id, model));
CREATE INDEX IF NOT EXISTS idx_turn_vectors ON turn_vectors(convo, model);
"""

class Database:
    def __init__(self, path: str):
        self._c = sqlite3.connect(path, check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self._c.executescript(_SCHEMA); self._c.commit()

    def create_session(self, title: str, workdir: str, model: str) -> int:
        cur = self._c.execute("INSERT INTO sessions(title,workdir,model,created_at) VALUES(?,?,?,?)",
                              (title, workdir, model, time.time()))
        self._c.commit(); return cur.lastrowid

    def add_message(self, session_id: int, role: str, content: str) -> int:
        cur = self._c.execute("INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                              (session_id, role, content, time.time()))
        self._c.commit(); return cur.lastrowid

    def add_action(self, session_id, tool, proposed_args, decision, final_args, stdout, stderr, exit_code) -> int:
        cur = self._c.execute(
            "INSERT INTO actions(session_id,tool,proposed_args,decision,final_args,stdout,stderr,exit_code,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (session_id, tool, proposed_args, decision, final_args, stdout, stderr, exit_code, time.time()))
        self._c.commit(); return cur.lastrowid

    def get_messages(self, session_id: int) -> list[dict]:
        return [dict(r) for r in self._c.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,))]

    def get_actions(self, session_id: int) -> list[dict]:
        return [dict(r) for r in self._c.execute(
            "SELECT * FROM actions WHERE session_id=? ORDER BY id", (session_id,))]

    # --- conversation state (multi-turn) ---
    def add_turn(self, convo, role, content):
        cur = self._c.execute("INSERT INTO conversations(convo,role,content,created_at) VALUES(?,?,?,?)",
                              (convo, role, content, time.time()))
        self._c.commit(); return cur.lastrowid

    def recent_turns(self, convo, limit=10):
        rows = self._c.execute(
            "SELECT role,content FROM conversations WHERE convo=? ORDER BY id DESC LIMIT ?",
            (convo, limit)).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def clear_conversation(self, convo):
        self._c.execute("DELETE FROM conversations WHERE convo=?", (convo,))
        self._c.execute("DELETE FROM chat_summaries WHERE convo=?", (convo,))
        self._c.execute("DELETE FROM turn_vectors WHERE convo=?", (convo,))
        self._c.execute("DELETE FROM pending_links WHERE convo=?", (convo,)); self._c.commit()

    def turn_rows(self, convo, after_id=0):
        """All turns of a chat after a turn id, oldest first, with their ids."""
        return [dict(r) for r in self._c.execute(
            "SELECT id,role,content FROM conversations WHERE convo=? AND id>? ORDER BY id",
            (convo, after_id))]

    def recent_turn_rows(self, convo, limit=10):
        """Like recent_turns, with each turn's id."""
        rows = self._c.execute(
            "SELECT id,role,content FROM conversations WHERE convo=? ORDER BY id DESC LIMIT ?",
            (convo, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    # --- embeddings of chat turns (for recall by meaning) ---
    def turns_missing_vectors(self, convo, model, limit=64):
        return [dict(r) for r in self._c.execute(
            "SELECT c.id,c.content FROM conversations c LEFT JOIN turn_vectors v "
            "ON v.turn_id=c.id AND v.model=? WHERE c.convo=? AND v.turn_id IS NULL ORDER BY c.id LIMIT ?",
            (model, convo, limit))]

    def save_vectors(self, rows):
        """rows: (turn_id, model, convo, vec bytes)"""
        self._c.executemany("INSERT OR REPLACE INTO turn_vectors(turn_id,model,convo,vec) VALUES(?,?,?,?)", rows)
        self._c.commit()

    def turns_missing_vectors_all(self, model, limit=64):
        """Turns of every chat without a vector, newest first (recent chats are searched first)."""
        return [dict(r) for r in self._c.execute(
            "SELECT c.id,c.convo,c.content FROM conversations c LEFT JOIN turn_vectors v "
            "ON v.turn_id=c.id AND v.model=? WHERE v.turn_id IS NULL ORDER BY c.id DESC LIMIT ?",
            (model, limit))]

    def other_vector_rows(self, model, exclude_convo, limit=5000):
        """Embedded turns of every other chat, newest first."""
        return [dict(r) for r in self._c.execute(
            "SELECT c.id,c.convo,c.role,c.content,c.created_at,v.vec FROM turn_vectors v "
            "JOIN conversations c ON c.id=v.turn_id WHERE v.model=? AND c.convo<>? ORDER BY c.id DESC LIMIT ?",
            (model, exclude_convo, limit))]

    def neighbour_turn(self, convo, turn_id, after):
        """The turn right after (or before) turn_id in the same chat."""
        sql = ("SELECT id,convo,role,content,created_at FROM conversations WHERE convo=? AND id>? ORDER BY id LIMIT 1"
               if after else
               "SELECT id,convo,role,content,created_at FROM conversations WHERE convo=? AND id<? ORDER BY id DESC LIMIT 1")
        r = self._c.execute(sql, (convo, turn_id)).fetchone()
        return dict(r) if r else None

    def vector_rows(self, convo, model):
        return [dict(r) for r in self._c.execute(
            "SELECT c.id,c.role,c.content,v.vec FROM turn_vectors v JOIN conversations c ON c.id=v.turn_id "
            "WHERE v.convo=? AND v.model=? ORDER BY c.id", (convo, model))]

    # --- rolling summary of the turns older than the recent window ---
    def get_summary(self, convo):
        r = self._c.execute("SELECT text,upto_id FROM chat_summaries WHERE convo=?", (convo,)).fetchone()
        return {"text": r["text"], "upto_id": r["upto_id"]} if r else None

    def set_summary(self, convo, text, upto_id):
        self._c.execute("INSERT OR REPLACE INTO chat_summaries(convo,upto_id,text,updated_at) VALUES(?,?,?,?)",
                        (convo, upto_id, text, time.time()))
        self._c.commit()

    # --- link intake: the follow-up question waiting for the user's choice ---
    def set_pending_link(self, convo, data: dict):
        self._c.execute("INSERT OR REPLACE INTO pending_links(convo,data,created_at) VALUES(?,?,?)",
                        (convo, json.dumps(data), time.time()))
        self._c.commit()

    def get_pending_link(self, convo):
        r = self._c.execute("SELECT data FROM pending_links WHERE convo=?", (convo,)).fetchone()
        return json.loads(r["data"]) if r else None

    def clear_pending_link(self, convo):
        self._c.execute("DELETE FROM pending_links WHERE convo=?", (convo,)); self._c.commit()

    def search_turns(self, convo, query, limit=10):
        toks = [t.lower() for t in (query or "").split() if t]
        rows = self._c.execute(
            "SELECT role,content FROM conversations WHERE convo=? ORDER BY id DESC", (convo,)).fetchall()
        out = []
        for r in rows:
            hay = r["content"].lower()
            if any(t in hay for t in toks):
                out.append({"role": r["role"], "content": r["content"]})
            if len(out) >= limit:
                break
        return out
