import base64, pytest
from agent.attachments import (AttachmentError, save_attachments, attachment_note, image_parts,
                               supports_vision, MAX_FILES)

def _item(name, data: bytes, type_=""):
    return {"name": name, "type": type_, "data": base64.b64encode(data).decode()}

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

def test_saves_files_under_uploads(tmp_path):
    out = save_attachments([_item("notes.txt", b"hello world", "text/plain")], tmp_path)
    assert len(out) == 1
    a = out[0]
    assert a.path.parent == tmp_path / "uploads"
    assert a.path.read_bytes() == b"hello world"
    assert a.name == "notes.txt" and a.size == 11 and not a.is_image

def test_filename_cannot_escape_uploads(tmp_path):
    a = save_attachments([_item("../../etc/passwd", b"x")], tmp_path)[0]
    assert a.path.parent == tmp_path / "uploads" and a.path.name.endswith("passwd")
    b = save_attachments([_item("..\\..\\win.ini", b"x")], tmp_path)[0]
    assert b.path.parent == tmp_path / "uploads" and "\\" not in b.path.name

def test_same_name_twice_does_not_overwrite(tmp_path):
    a, b = save_attachments([_item("a.txt", b"one"), _item("a.txt", b"two")], tmp_path)
    assert a.path != b.path
    assert a.path.read_bytes() == b"one" and b.path.read_bytes() == b"two"

def test_empty_or_missing_name_gets_a_default(tmp_path):
    a = save_attachments([_item("", b"x")], tmp_path)[0]
    assert a.path.name and a.path.parent == tmp_path / "uploads"

def test_image_detected_by_type_or_extension(tmp_path):
    a, b = save_attachments([_item("pic.png", PNG, "image/png"), _item("photo.JPG", PNG)], tmp_path)
    assert a.is_image and a.mime == "image/png"
    assert b.is_image and b.mime == "image/jpeg"

def test_invalid_base64_rejected(tmp_path):
    with pytest.raises(AttachmentError):
        save_attachments([{"name": "x.txt", "type": "", "data": "not base64!!"}], tmp_path)

def test_too_many_files_rejected(tmp_path):
    with pytest.raises(AttachmentError):
        save_attachments([_item(f"{i}.txt", b"x") for i in range(MAX_FILES + 1)], tmp_path)
    assert not (tmp_path / "uploads").exists() or not any((tmp_path / "uploads").iterdir())

def test_oversize_file_rejected(tmp_path, monkeypatch):
    import agent.attachments as at
    monkeypatch.setattr(at, "MAX_FILE_BYTES", 10)
    with pytest.raises(AttachmentError):
        save_attachments([_item("big.bin", b"x" * 11)], tmp_path)

def test_note_lists_paths_and_inlines_small_text(tmp_path):
    atts = save_attachments([_item("notes.txt", b"the secret word is banana", "text/plain"),
                             _item("blob.bin", bytes(range(256)) * 4)], tmp_path)
    note = attachment_note(atts)
    assert str(atts[0].path) in note and str(atts[1].path) in note
    assert "the secret word is banana" in note
    assert "\x00" not in note

def test_note_does_not_inline_large_text(tmp_path, monkeypatch):
    import agent.attachments as at
    monkeypatch.setattr(at, "INLINE_TEXT_BYTES", 5)
    atts = save_attachments([_item("long.txt", b"abcdefghij", "text/plain")], tmp_path)
    note = attachment_note(atts)
    assert "abcdefghij" not in note and str(atts[0].path) in note

def test_note_marks_images_seen_or_not(tmp_path):
    atts = save_attachments([_item("pic.png", PNG, "image/png")], tmp_path)
    assert "cannot see" not in attachment_note(atts, vision=True)
    blind = attachment_note(atts, vision=False)
    assert "cannot see" in blind and str(atts[0].path) in blind

def test_note_shows_readable_sizes(tmp_path):
    atts = save_attachments([_item("a.bin", b"x" * 500), _item("b.bin", b"x" * 5120),
                             _item("c.bin", b"x" * (3 * 1024 * 1024))], tmp_path)
    note = attachment_note(atts)
    assert "500 B" in note and "5.0 KB" in note and "3.0 MB" in note

def test_note_empty_without_attachments():
    assert attachment_note([]) == ""

def test_image_parts_are_data_urls(tmp_path):
    atts = save_attachments([_item("pic.png", PNG, "image/png"), _item("n.txt", b"hi")], tmp_path)
    parts = image_parts(atts)
    assert len(parts) == 1
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert base64.b64decode(parts[0]["image_url"]["url"].split(",", 1)[1]) == PNG

class _Resp:
    def __init__(self, code, body): self.status_code = code; self._b = body
    def json(self): return self._b

class _Client:
    def __init__(self, resp): self.resp = resp; self.url = None; self.body = None
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def post(self, url, json=None): self.url = url; self.body = json; return self.resp

@pytest.mark.asyncio
async def test_supports_vision_reads_capabilities(monkeypatch):
    import agent.attachments as at
    c = _Client(_Resp(200, {"capabilities": ["completion", "tools", "vision"]}))
    monkeypatch.setattr(at.httpx, "AsyncClient", lambda **k: c)
    assert await supports_vision("http://localhost:11434/v1", "m") is True
    assert c.url == "http://localhost:11434/api/show" and c.body == {"model": "m"}

@pytest.mark.asyncio
async def test_supports_vision_false_without_capability_or_on_error(monkeypatch):
    import agent.attachments as at
    monkeypatch.setattr(at.httpx, "AsyncClient", lambda **k: _Client(_Resp(200, {"capabilities": ["tools"]})))
    assert await supports_vision("http://h/v1", "m") is False
    monkeypatch.setattr(at.httpx, "AsyncClient", lambda **k: _Client(_Resp(404, {})))
    assert await supports_vision("http://h/v1", "m") is False
