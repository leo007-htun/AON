"""Document library (RAG over the user's files).

Files the user adds (the Documents panel, chat attachments, or the add_document tool) are turned
into text, split into overlapping chunks and stored in docs.db with a nomic-embed-text vector per
chunk and an FTS5 keyword index. A question is matched both ways and the two rankings are merged
(reciprocal rank fusion): meaning finds paraphrases, keywords find exact strings such as IPs,
codes and numbers, which embeddings match poorly. The best chunks go to the model as background.
"""
from __future__ import annotations
import hashlib
import html
import io
import re
import sqlite3
import time
import zipfile
from pathlib import Path
from aon.context import clip_text, estimate_tokens
from aon.recall import DOC, QUERY, _pack, _unpack, _unit, exact_terms
from aon.tools.base import Tool, ToolResult

CHUNK_CHARS = 1600          # about 400 tokens: small enough to be specific, big enough to stand alone
OVERLAP = 200               # carried into the next chunk so a sentence on the boundary is in both
MAX_DOC_CHARS = 3_000_000
EMBED_BATCH = 16
MIN_SCORE = 0.55            # a chunk is sent unasked only at this similarity or with an exact match;
                            # measured with nomic-embed-text on 3 documents and 20 questions: related
                            # 0.562-0.737 (all found the right document), unrelated 0.441-0.543
RRF_K = 60
CHUNK_TOKENS = 500          # a chunk as it is sent to the model

TEXT_EXT = {".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml", ".ini",
            ".cfg", ".conf", ".log", ".xml", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c",
            ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".sh", ".bash", ".ps1", ".sql", ".css", ".scss",
            ".kt", ".swift", ".lua", ".r", ".m", ".tex", ".env", ".properties"}
HTML_EXT = {".html", ".htm"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, source TEXT, sha TEXT, chars INTEGER,
  chunks INTEGER, added_at REAL);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER, ord INTEGER, text TEXT);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id, ord);
CREATE TABLE IF NOT EXISTS chunk_vectors(
  chunk_id INTEGER, model TEXT, vec BLOB, PRIMARY KEY(chunk_id, model));
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, tokenize='unicode61');
"""


class DocError(ValueError):
    pass


# --- turning a file into text -------------------------------------------------------------------

def _decode(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            raise DocError("unreadable UTF-16 text")
    if b"\x00" in data[:8192]:
        raise DocError("binary file")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _html_text(text: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|section|article)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text)


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError):
        raise DocError("not a readable .docx file")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise DocError("PDF support needs the pypdf package (venv/bin/pip install pypdf)")
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(p.extract_text() or "") for p in reader.pages]
    except Exception as e:
        raise DocError(f"could not read the PDF ({type(e).__name__})")
    text = "\n\n".join(pages)
    if not text.strip():
        raise DocError("the PDF has no text layer (a scan); it would need OCR")
    return text


def extract_text(name: str, data: bytes) -> str:
    ext = Path(name or "").suffix.lower()
    if ext == ".pdf" or data[:5] == b"%PDF-":
        text = _pdf_text(data)
    elif ext == ".docx":
        text = _docx_text(data)
    elif ext in HTML_EXT:
        text = _html_text(_decode(data))
    elif ext in TEXT_EXT or not ext:
        text = _decode(data)
    else:
        try:
            text = _decode(data)
        except DocError:
            raise DocError(f"unsupported file type {ext!r}")
    text = re.sub(r"[ \t\f\v]+\n", "\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise DocError("no text found in the file")
    return text[:MAX_DOC_CHARS]


# --- chunks ----------------------------------------------------------------------------------------

def _cut_point(text: str, start: int, end: int) -> int:
    """The best place to end a chunk in text[start:end]: a paragraph, then a line, then a sentence,
    then a space, in the second half of the window."""
    window = text[start:end]
    half = len(window) // 2
    for sep in ("\n\n", "\n", ". ", "? ", "! ", "; ", " "):
        i = window.rfind(sep, half)
        if i != -1:
            return start + i + len(sep)
    return end


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP) -> list[str]:
    text = (text or "").strip()
    if len(text) <= size:
        return [text] if text else []
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            end = _cut_point(text, start, end)
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= len(text):
            break
        nxt = max(start + 1, end - overlap)
        sp = text.find(" ", nxt, end)        # start the overlap on a word boundary
        start = sp + 1 if sp != -1 else nxt
    return out


# --- storage ----------------------------------------------------------------------------------------

class DocStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._c = sqlite3.connect(str(path), check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self._c.executescript(_SCHEMA); self._c.commit()

    def find(self, name: str, sha: str):
        r = self._c.execute("SELECT * FROM documents WHERE name=? AND sha=?", (name, sha)).fetchone()
        return dict(r) if r else None

    def add(self, name: str, source: str, sha: str, text: str, chunks: list[str]) -> int:
        cur = self._c.execute("INSERT INTO documents(name,source,sha,chars,chunks,added_at) VALUES(?,?,?,?,?,?)",
                              (name, source, sha, len(text), len(chunks), time.time()))
        doc_id = cur.lastrowid
        for i, t in enumerate(chunks):
            cid = self._c.execute("INSERT INTO chunks(doc_id,ord,text) VALUES(?,?,?)", (doc_id, i, t)).lastrowid
            self._c.execute("INSERT INTO chunks_fts(rowid,text) VALUES(?,?)", (cid, t))
        self._c.commit()
        return doc_id

    def list(self) -> list[dict]:
        return [dict(r) for r in self._c.execute(
            "SELECT id,name,source,chars,chunks,added_at FROM documents ORDER BY id DESC")]

    def get(self, doc_id: int):
        r = self._c.execute("SELECT id,name,source,chars,chunks,added_at FROM documents WHERE id=?",
                            (doc_id,)).fetchone()
        return dict(r) if r else None

    def delete(self, doc_id: int) -> bool:
        ids = [r[0] for r in self._c.execute("SELECT id FROM chunks WHERE doc_id=?", (doc_id,))]
        for cid in ids:
            self._c.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
            self._c.execute("DELETE FROM chunk_vectors WHERE chunk_id=?", (cid,))
        self._c.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        n = self._c.execute("DELETE FROM documents WHERE id=?", (doc_id,)).rowcount
        self._c.commit()
        return n > 0

    def missing_vectors(self, model: str, limit: int = 256) -> list[dict]:
        return [dict(r) for r in self._c.execute(
            "SELECT c.id,c.text FROM chunks c LEFT JOIN chunk_vectors v ON v.chunk_id=c.id AND v.model=? "
            "WHERE v.chunk_id IS NULL ORDER BY c.id LIMIT ?", (model, limit))]

    def save_vectors(self, rows):
        """rows: (chunk_id, model, vec bytes)"""
        self._c.executemany("INSERT OR REPLACE INTO chunk_vectors(chunk_id,model,vec) VALUES(?,?,?)", rows)
        self._c.commit()

    def vector_rows(self, model: str) -> list[dict]:
        return [dict(r) for r in self._c.execute(
            "SELECT c.id,c.doc_id,c.ord,v.vec FROM chunk_vectors v JOIN chunks c ON c.id=v.chunk_id "
            "WHERE v.model=?", (model,))]

    def keyword_ids(self, match: str, limit: int = 20) -> list[int]:
        try:
            return [r[0] for r in self._c.execute(
                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
                (match, limit))]
        except sqlite3.OperationalError:
            return []

    def chunk(self, chunk_id: int):
        r = self._c.execute("SELECT c.id,c.doc_id,c.ord,c.text,d.name FROM chunks c JOIN documents d "
                            "ON d.id=c.doc_id WHERE c.id=?", (chunk_id,)).fetchone()
        return dict(r) if r else None

    def chunk_text_of(self, chunk_id: int) -> str:
        r = self._c.execute("SELECT text FROM chunks WHERE id=?", (chunk_id,)).fetchone()
        return r[0] if r else ""


async def index_chunks(store: DocStore, embedder, limit: int = 4096) -> int:
    """Embed chunks that have no vector yet. Returns how many."""
    total = 0
    while total < limit:
        rows = store.missing_vectors(embedder.model, min(256, limit - total))
        if not rows:
            break
        for i in range(0, len(rows), EMBED_BATCH):
            batch = rows[i:i + EMBED_BATCH]
            vecs = await embedder.embed([DOC + r["text"] for r in batch])
            store.save_vectors([(r["id"], embedder.model, _pack(_unit(v))) for r, v in zip(batch, vecs)])
            total += len(batch)
    return total


async def ingest(store: DocStore, embedder, name: str, data: bytes, source: str = "upload") -> dict:
    """Add a file to the library (the same name and content is not added twice). Returns the
    document row, with "existing": True when it was already there."""
    name = (re.split(r"[\\/]", name or "")[-1] or "document")[:200]
    sha = hashlib.sha256(data).hexdigest()
    old = store.find(name, sha)
    if old:
        return dict(old, existing=True)
    text = extract_text(name, data)
    pieces = chunk_text(text)
    doc_id = store.add(name, source, sha, text, pieces)
    if embedder is not None:
        try:
            await index_chunks(store, embedder)
        except Exception:
            pass        # keyword search still works; vectors are added on the next search
    return dict(store.get(doc_id), existing=False)


# --- search -----------------------------------------------------------------------------------------

_STOP = set("""a an and are as at be but by can could did do does for from had has have how i if in into is
it its me my no not of on or our please should so that the their them then there these they this to us was
we were what when where which who why will with would you your about tell show give find any all some
document documents file files doc docs""".split())


def fts_query(text: str) -> str:
    """An FTS5 OR-query of the question's meaningful words; quoted so punctuation (IPs, versions,
    hyphenated codes) is matched as a phrase and never parsed as FTS syntax."""
    terms, seen = [], set()
    for tok in re.findall(r"[\w][\w.\-:/@]*[\w]|\w", text or ""):
        low = tok.lower()
        if low in _STOP or low in seen or (len(low) < 3 and not any(ch.isdigit() for ch in low)):
            continue
        seen.add(low)
        terms.append('"' + tok.replace('"', "") + '"')
    return " OR ".join(terms[:24])


async def search(store: DocStore, embedder, query: str, k: int = 6) -> list[dict]:
    """The chunks that best answer query, best first: vector and keyword rankings merged.
    Each hit: chunk_id, doc_id, name, ord, text, score (cosine, or None), keyword (bool)."""
    if not (query or "").strip():
        return []
    vec_rank, scores = [], {}
    if embedder is not None:
        try:
            await index_chunks(store, embedder)
            q = _unit((await embedder.embed([QUERY + query[:6000]]))[0])
            for r in store.vector_rows(embedder.model):
                v = _unpack(r["vec"])
                if len(v) == len(q):
                    scores[r["id"]] = sum(a * b for a, b in zip(q, v))
            vec_rank = sorted(scores, key=lambda i: -scores[i])[:max(20, k * 3)]
        except Exception:
            vec_rank = []
    match = fts_query(query)
    kw_rank = store.keyword_ids(match, max(20, k * 3)) if match else []
    exact = [t.lower() for t in exact_terms(query)]
    fused: dict[int, float] = {}
    for rank_list in (vec_rank, kw_rank):
        for pos, cid in enumerate(rank_list):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + pos + 1)
    hits = []
    for cid in sorted(fused, key=lambda i: -fused[i]):
        c = store.chunk(cid)
        if not c:
            continue
        has_exact = bool(exact) and all(t in c["text"].lower() for t in exact)
        hits.append({"chunk_id": cid, "doc_id": c["doc_id"], "name": c["name"], "ord": c["ord"],
                     "text": c["text"], "score": round(scores[cid], 3) if cid in scores else None,
                     "keyword": cid in kw_rank, "exact": has_exact})
    # a chunk holding every exact string asked about (an IP, a code) goes first
    hits.sort(key=lambda h: (not h["exact"],))
    return hits[:k]


def _relevant(h: dict, min_score: float) -> bool:
    return h["exact"] or (h["score"] is not None and h["score"] >= min_score)


async def docs_background(store: DocStore, embedder, query: str, budget: int = 1500,
                          min_score: float = MIN_SCORE) -> str:
    """Background for a request: library passages relevant enough to send unasked."""
    if store is None or not store.list():
        return ""
    try:
        hits = [h for h in await search(store, embedder, query, k=8) if _relevant(h, min_score)]
    except Exception:
        return ""
    if not hits:
        return ""
    lines, used = [], 0
    for h in hits:
        room = min(CHUNK_TOKENS, budget - used - 12)      # 12: the "[name #n]" label
        if room < 60:
            break
        text = clip_text(h["text"], room)
        n = estimate_tokens(text) + 12
        if not text or used + n > budget:
            break
        lines.append(f"[{h['name']} #{h['ord'] + 1}]\n{text}")
        used += n
    if not lines:
        return ""
    return ("Passages from the user's document library that may be relevant (cite the document name "
            "when you use one; call search_documents for more):\n\n" + "\n\n".join(lines))


# --- tools ------------------------------------------------------------------------------------------

class SearchDocumentsTool(Tool):
    name = "search_documents"
    description = ("Search the user's document library (files they added) by meaning and keywords; "
                   "returns the best passages with document names.")
    parameters = {"type": "object", "properties": {"query": {"type": "string"},
                                                   "limit": {"type": "integer"}}, "required": ["query"]}
    mutating = False

    def __init__(self, store: DocStore, embedder):
        self.store, self.embedder = store, embedder

    async def run(self, args):
        if not self.store.list():
            return ToolResult(ok=True, output="(the document library is empty)")
        k = max(1, min(int(args.get("limit") or 5), 10))
        hits = await search(self.store, self.embedder, args["query"], k=k)
        if not hits:
            return ToolResult(ok=True, output="(no matching passages)")
        return ToolResult(ok=True, output="\n\n".join(
            f"[{h['name']} #{h['ord'] + 1}] (score {h['score']}{', keyword match' if h['keyword'] else ''})\n"
            f"{clip_text(h['text'], CHUNK_TOKENS)}" for h in hits))


class ListDocumentsTool(Tool):
    name = "list_documents"
    description = "List the documents in the user's document library."
    parameters = {"type": "object", "properties": {}}
    mutating = False

    def __init__(self, store: DocStore):
        self.store = store

    async def run(self, args):
        docs = self.store.list()
        if not docs:
            return ToolResult(ok=True, output="(the document library is empty)")
        return ToolResult(ok=True, output="\n".join(
            f"- {d['name']} ({d['chars']} chars, {d['chunks']} passages, from {d['source']})" for d in docs))


class AddDocumentTool(Tool):
    name = "add_document"
    description = ("Add a file from the workspace to the document library so it can be searched later "
                   "(text, code, Markdown, HTML, .docx, PDF). path is workspace-relative.")
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    mutating = False

    def __init__(self, store: DocStore, embedder, workdir):
        self.store, self.embedder, self.workdir = store, embedder, Path(workdir).resolve()

    async def run(self, args):
        p = (self.workdir / str(args["path"]).lstrip("/")).resolve()
        if self.workdir not in p.parents and p != self.workdir:
            return ToolResult(ok=False, output="", error="path is outside the workspace")
        if not p.is_file():
            return ToolResult(ok=False, output="", error=f"no such file: {args['path']}")
        try:
            d = await ingest(self.store, self.embedder, p.name, p.read_bytes(), source="workspace")
        except DocError as e:
            return ToolResult(ok=False, output="", error=str(e))
        if d.get("existing"):
            return ToolResult(ok=True, output=f"{d['name']} is already in the library.")
        n = d["chunks"]
        return ToolResult(ok=True, output=f"Added {d['name']} to the library ({n} passage{'' if n == 1 else 's'}).")
