"""Document library (RAG): extraction, chunking, hybrid search, background, tools and the web API;
plus the chat-side additions: exact-string recall and the outline of earlier messages."""
import base64
import io
import re
import zipfile
import zlib
import pytest
from fastapi.testclient import TestClient
from aon.docs import (DocStore, DocError, extract_text, chunk_text, ingest, search, docs_background,
                        fts_query, SearchDocumentsTool, ListDocumentsTool, AddDocumentTool, CHUNK_CHARS)
from aon.recall import exact_terms, recall, _pack, _unit
from aon.summary import outline_background
from aon.persistence import Database
from aon.config import Config
from aon.web import create_app
from aon.engine import RunResult


class FakeEmbedder:
    """Bag of words hashed into 256 dimensions: texts sharing words score high."""
    model = "fake"
    def __init__(self, fail=False): self.fail = fail; self.calls = 0
    async def embed(self, texts):
        self.calls += 1
        if self.fail:
            raise RuntimeError("embedder down")
        out = []
        for t in texts:
            t = re.sub(r"^search_(document|query): ", "", t)
            v = [0.0] * 256
            for w in re.findall(r"[a-z]{3,}", t.lower()):
                v[zlib.crc32(w.encode()) % 256] += 1.0
            out.append(_unit(v))
        return out


def _docx(paragraphs):
    xml = ('<?xml version="1.0"?><w:document xmlns:w="w"><w:body>' +
           "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs) + "</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


# --- extraction -------------------------------------------------------------------------------------

def test_extract_plain_text_and_code():
    assert extract_text("notes.md", b"# Title\r\n\r\n\r\n\r\nbody") == "# Title\n\nbody"
    assert "def f" in extract_text("x.py", b"def f():\n    return 1\n")


def test_extract_html_drops_scripts_and_tags():
    t = extract_text("p.html", b"<html><script>evil()</script><p>Hello &amp; welcome</p><div>bye</div></html>")
    assert "evil" not in t and "Hello & welcome" in t and "bye" in t and "<" not in t


def test_extract_docx_without_extra_packages():
    t = extract_text("r.docx", _docx(["First paragraph", "Second &amp; last"]))
    assert t.splitlines() == ["First paragraph", "Second & last"]


def test_binary_and_empty_files_are_refused():
    with pytest.raises(DocError):
        extract_text("blob.bin", b"\x00\x01\x02" * 100)
    with pytest.raises(DocError):
        extract_text("e.txt", b"   \n  ")
    assert extract_text("u16.txt", "héllo wörld".encode("utf-16")) == "héllo wörld"
    assert extract_text("latin.txt", "café".encode("latin-1")) == "café"


def _pdf(text):
    """A one-page PDF with a real text layer (Helvetica), xref offsets computed."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out)); out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def test_pdf_text_is_extracted():
    pytest.importorskip("pypdf")
    assert "Invoice 4242 total 99 GBP" in extract_text("inv.pdf", _pdf("Invoice 4242 total 99 GBP"))


def test_pdf_without_a_text_layer_or_library_gives_a_clear_error():
    with pytest.raises(DocError) as e:
        extract_text("scan.pdf", b"%PDF-1.4 not really a pdf")
    assert "PDF" in str(e.value) or "pdf" in str(e.value)


# --- chunks -----------------------------------------------------------------------------------------

def test_short_text_is_one_chunk():
    assert chunk_text("just a line") == ["just a line"]


def test_long_text_is_split_at_boundaries_with_overlap():
    sentences = [f"Sentence number {i} talks about topic {i}." for i in range(300)]
    text = " ".join(sentences)
    chunks = chunk_text(text)
    assert len(chunks) > 3 and all(len(c) <= CHUNK_CHARS for c in chunks)
    for s in sentences:                              # nothing is lost
        assert any(s in c for c in chunks), s
    assert all(c.endswith(".") for c in chunks[:-1])   # cut after a sentence, not mid-word
    tail = chunks[0][-60:].split(". ")[-1]
    assert tail in chunks[1]                          # the boundary sentence is in both


# --- storage and search -------------------------------------------------------------------------

DOC_A = ("Staging runbook. The staging database lives at 10.40.7.22 on port 6543. "
         "Backups run nightly at 02:00. ") + "Filler about unrelated gardening tips. " * 80
DOC_B = "Recipe book. To bake sourdough bread, feed the starter, knead the dough and bake it hot. " * 20


@pytest.mark.asyncio
async def test_ingest_stores_chunks_and_vectors_and_skips_duplicates(tmp_path):
    store, emb = DocStore(tmp_path / "docs.db"), FakeEmbedder()
    d = await ingest(store, emb, "runbook.txt", DOC_A.encode())
    assert d["existing"] is False and d["chunks"] >= 2 and d["name"] == "runbook.txt"
    assert store.missing_vectors("fake") == []
    again = await ingest(store, emb, "runbook.txt", DOC_A.encode())
    assert again["existing"] is True and len(store.list()) == 1


@pytest.mark.asyncio
async def test_ingest_without_embedder_still_searchable_by_keyword(tmp_path):
    store = DocStore(tmp_path / "docs.db")
    await ingest(store, FakeEmbedder(fail=True), "runbook.txt", DOC_A.encode())
    hits = await search(store, FakeEmbedder(fail=True), "where is 10.40.7.22")
    assert hits and "10.40.7.22" in hits[0]["text"] and hits[0]["exact"]


@pytest.mark.asyncio
async def test_search_finds_by_meaning_and_names_the_document(tmp_path):
    store, emb = DocStore(tmp_path / "docs.db"), FakeEmbedder()
    await ingest(store, emb, "runbook.txt", DOC_A.encode())
    await ingest(store, emb, "recipes.md", DOC_B.encode())
    hits = await search(store, emb, "how do I bake sourdough bread", k=5)
    assert hits[0]["name"] == "recipes.md"
    other = [h["score"] for h in hits if h["name"] == "runbook.txt" and h["score"] is not None]
    assert all(hits[0]["score"] > s for s in other)


@pytest.mark.asyncio
async def test_exact_strings_beat_meaning(tmp_path):
    store, emb = DocStore(tmp_path / "docs.db"), FakeEmbedder()
    await ingest(store, emb, "runbook.txt", DOC_A.encode())
    await ingest(store, emb, "recipes.md", DOC_B.encode())
    hits = await search(store, emb, "port 6543", k=3)
    assert hits[0]["name"] == "runbook.txt" and "6543" in hits[0]["text"] and hits[0]["exact"]


def test_fts_query_never_breaks_on_punctuation():
    for q in ['what is at 10.40.7.22?', 'he said "hi" (ok) AND NOT', "x-y_z: a/b @c", "", "the of and"]:
        m = fts_query(q)
        assert '""' not in m


@pytest.mark.asyncio
async def test_odd_queries_do_not_raise(tmp_path):
    store, emb = DocStore(tmp_path / "docs.db"), FakeEmbedder()
    await ingest(store, emb, "runbook.txt", DOC_A.encode())
    for q in ['he said "hi" AND (NOT', "*", "NEAR(", "the of and"]:
        await search(store, emb, q)


@pytest.mark.asyncio
async def test_delete_removes_everything(tmp_path):
    store, emb = DocStore(tmp_path / "docs.db"), FakeEmbedder()
    d = await ingest(store, emb, "runbook.txt", DOC_A.encode())
    assert store.delete(d["id"]) is True
    assert store.list() == [] and store.vector_rows("fake") == []
    assert store.keyword_ids('"6543"') == []
    assert await search(store, emb, "6543") == []


# --- background ---------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_background_only_with_relevant_passages(tmp_path):
    store, emb = DocStore(tmp_path / "docs.db"), FakeEmbedder()
    assert await docs_background(store, emb, "anything") == ""            # empty library
    await ingest(store, emb, "runbook.txt", DOC_A.encode())
    assert await docs_background(store, emb, "quantum chromodynamics lecture") == ""
    bg = await docs_background(store, emb, "which port is the staging database on, 6543?")
    assert "runbook.txt" in bg and "6543" in bg


@pytest.mark.asyncio
async def test_background_respects_its_budget(tmp_path):
    store, emb = DocStore(tmp_path / "docs.db"), FakeEmbedder()
    await ingest(store, emb, "recipes.md", DOC_B.encode())
    bg = await docs_background(store, emb, "bake sourdough bread starter dough", budget=300, min_score=0.3)
    assert bg and len(bg) < 300 * 3 + 400


# --- tools --------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tools(tmp_path):
    store, emb = DocStore(tmp_path / "docs.db"), FakeEmbedder()
    ws = tmp_path / "ws"; ws.mkdir(); (ws / "notes.txt").write_text(DOC_A)
    assert "empty" in (await SearchDocumentsTool(store, emb).run({"query": "x"})).output
    add = AddDocumentTool(store, emb, ws)
    r = await add.run({"path": "notes.txt"})
    assert r.ok and "Added notes.txt" in r.output
    assert "already" in (await add.run({"path": "notes.txt"})).output
    assert not (await add.run({"path": "../../etc/passwd"})).ok
    assert not (await add.run({"path": "missing.txt"})).ok
    assert "notes.txt" in (await ListDocumentsTool(store).run({})).output
    out = (await SearchDocumentsTool(store, emb).run({"query": "10.40.7.22"})).output
    assert "[notes.txt #" in out and "10.40.7.22" in out


# --- web API ------------------------------------------------------------------------------------

def _app(tmp, store, emb, seen=None):
    db = Database(str(tmp / "a.db"))
    class FakeAgent:
        def __init__(self, on_event): self.on_event = on_event
        async def run(self, goal, conversation=None, background="", **kw):
            if seen is not None:
                seen.append(background)
            await self.on_event({"type": "final", "status": "done", "final": "ok"})
            return RunResult("done", "ok", [])
    def make_agent(gate, on_event, model, convo="default"): return FakeAgent(on_event)
    return create_app(Config("http://x/v1", "m", tmp, [], 5, 10, "127.0.0.1:8700"), db, make_agent,
                      embedder=emb, docs=store), db


def test_web_add_list_delete(tmp_path):
    store, emb = DocStore(tmp_path / "docs.db"), FakeEmbedder()
    app, _ = _app(tmp_path, store, emb)
    c = TestClient(app)
    r = c.post("/api/docs", json={"name": "runbook.txt", "data": base64.b64encode(DOC_A.encode()).decode()}).json()
    assert r["document"]["name"] == "runbook.txt" and r["document"]["chunks"] >= 2
    docs = c.get("/api/docs").json()["documents"]
    assert [d["name"] for d in docs] == ["runbook.txt"]
    assert "error" in c.post("/api/docs", json={"name": "x.txt", "data": "%%%"}).json()
    assert "error" in c.post("/api/docs", json={"name": "b.bin", "data": base64.b64encode(b"\x00" * 50).decode()}).json()
    assert c.delete(f"/api/docs/{docs[0]['id']}").json()["ok"] is True
    assert c.get("/api/docs").json()["documents"] == []


def test_chat_attachment_goes_into_the_library_and_passages_reach_the_agent(tmp_path):
    store, emb = DocStore(tmp_path / "docs.db"), FakeEmbedder()
    seen = []
    app, _ = _app(tmp_path, store, emb, seen)
    c = TestClient(app)
    notices = []
    with c.websocket_connect("/ws") as ws:
        ws.send_json({"goal": "keep this", "model": "m", "session_id": "s1",
                      "attachments": [{"name": "runbook.txt", "type": "text/plain",
                                       "data": base64.b64encode(DOC_A.encode()).decode()}]})
        while True:
            ev = ws.receive_json()
            if ev.get("type") == "notice":
                notices.append(ev["text"])
            if ev.get("type") == "final":
                break
    assert any("runbook.txt" in n and "library" in n for n in notices)
    with c.websocket_connect("/ws") as ws:
        ws.send_json({"goal": "what port is the staging database on, 6543?", "model": "m", "session_id": "s2"})
        while ws.receive_json().get("type") != "final":
            pass
    assert "runbook.txt" in seen[-1] and "6543" in seen[-1]


# --- chats: exact strings and the outline --------------------------------------------------------

def test_exact_terms():
    assert exact_terms("is HALCYON-7 at 10.40.7.22 port 6543 per v1.2?") == ["HALCYON-7", "10.40.7.22", "6543", "v1.2"]
    assert exact_terms("what did we agree on") == []
    assert exact_terms("what is the API rate limit over HTTP for HALCYON") == ["HALCYON"]


@pytest.mark.asyncio
async def test_recall_puts_turns_with_the_exact_string_first_even_without_embedder(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    ids = [db.add_turn("c", "user", f"chatting about topic {i} at some length") for i in range(10)]
    hit = db.add_turn("c", "user", "the staging box is 10.40.7.22 remember it")
    db.add_turn("c", "assistant", "Noted, staging is 10.40.7.22.")
    emb = FakeEmbedder()
    rows = db.turns_missing_vectors("c", "fake")
    vecs = await emb.embed([r["content"] for r in rows])
    db.save_vectors([(r["id"], "fake", "c", _pack(v)) for r, v in zip(rows, vecs)])
    hits = await recall(db, FakeEmbedder(fail=True), "c", "what was at 10.40.7.22?", exclude_ids=set())
    assert hits and any(h["id"] == hit for h in hits)


def test_outline_lists_earlier_user_messages_in_order(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    a = db.add_turn("c", "user", "Explain TCP congestion control please")
    db.add_turn("c", "assistant", "TCP uses slow start ...")
    b = db.add_turn("c", "user", "Now B-trees\n\n[Attached files, saved in the workspace]\n- x.txt")
    db.add_turn("c", "assistant", "B-trees are ...")
    recent = db.add_turn("c", "user", "and DNS?")
    out = outline_background(db, "c", exclude_ids={recent})
    lines = out.splitlines()[1:]
    # numbered across the whole chat, recent messages included, so "the third one" is unambiguous
    assert lines == ["1. Explain TCP congestion control please", "2. Now B-trees", "3. and DNS?"]
    assert outline_background(db, "c", exclude_ids={a, b, recent}) == ""     # all still sent in full


def test_outline_keeps_the_first_and_latest_when_long(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    for i in range(200):
        db.add_turn("c", "user", f"message number {i} " + "word " * 30)
    out = outline_background(db, "c", exclude_ids=set(), budget=400)
    assert "1. message number 0 " in out and "5. message number 4 " in out
    assert "200. message number 199 " in out and "…\n" in out
    assert len(out) // 3 < 400 + 60
