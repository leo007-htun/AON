"""Files and photos attached to a chat goal: saved into the workspace, described to the agent,
and (for vision models) sent to the model as images."""
import base64, binascii, mimetypes, re, time
from dataclasses import dataclass
from pathlib import Path
import httpx

MAX_FILES = 10
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024
INLINE_TEXT_BYTES = 32 * 1024

_IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}


class AttachmentError(ValueError):
    pass


@dataclass
class Attachment:
    name: str
    path: Path
    mime: str
    size: int
    is_image: bool


def _safe_name(name: str) -> str:
    base = re.split(r"[\\/]", name or "")[-1]
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")
    return base[:120] or "file"


def _mime(name: str, given: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in _IMAGE_EXT:
        return _IMAGE_EXT[ext]
    return given or mimetypes.guess_type(name)[0] or "application/octet-stream"


def save_attachments(items: list[dict], workdir) -> list[Attachment]:
    """Decode and validate everything first, then write; a bad item saves nothing."""
    items = items or []
    if len(items) > MAX_FILES:
        raise AttachmentError(f"too many attachments ({len(items)}); the limit is {MAX_FILES}")
    decoded, total = [], 0
    for it in items:
        name = _safe_name(str(it.get("name", "")))
        try:
            data = base64.b64decode(it.get("data") or "", validate=True)
        except (binascii.Error, ValueError):
            raise AttachmentError(f"attachment {name!r} is not valid base64")
        if len(data) > MAX_FILE_BYTES:
            raise AttachmentError(f"attachment {name!r} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise AttachmentError(f"attachments are larger than {MAX_TOTAL_BYTES // (1024 * 1024)} MB in total")
        decoded.append((name, str(it.get("type") or ""), data))
    out = []
    if not decoded:
        return out
    up = Path(workdir) / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for name, given, data in decoded:
        path = up / f"{stamp}-{name}"
        n = 1
        while path.exists():
            path = up / f"{stamp}-{n}-{name}"; n += 1
        path.write_bytes(data)
        mime = _mime(name, given)
        out.append(Attachment(name, path, mime, len(data), mime.startswith("image/")))
    return out


def _fmt(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _inline_text(a: Attachment) -> str | None:
    if a.is_image or a.size > INLINE_TEXT_BYTES:
        return None
    raw = a.path.read_bytes()
    if b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def attachment_note(atts: list[Attachment], vision: bool = True) -> str:
    """Text appended to the goal so the agent knows what was attached and where it is."""
    if not atts:
        return ""
    lines = ["[Attached files, saved in the workspace]"]
    blocks = []
    for a in atts:
        desc = f"- {a.name} ({a.mime}, {_fmt(a.size)}) at {a.path}"
        if a.is_image:
            desc += " - image is shown to you below" if vision else \
                " - image; the current model cannot see images, only the file path"
        lines.append(desc)
        text = _inline_text(a)
        if text is not None:
            blocks.append(f"--- {a.name} ---\n{text}\n--- end of {a.name} ---")
    return "\n".join(lines + blocks)


def image_parts(atts: list[Attachment]) -> list[dict]:
    """OpenAI-style image content parts (Ollama's /v1 endpoint accepts data URLs)."""
    return [{"type": "image_url",
             "image_url": {"url": f"data:{a.mime};base64," + base64.b64encode(a.path.read_bytes()).decode()}}
            for a in atts if a.is_image]


async def supports_vision(base_url: str, model: str) -> bool:
    root = re.sub(r"/v1/?$", "", base_url.rstrip("/"))
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{root}/api/show", json={"model": model})
            if r.status_code != 200:
                return False
            return "vision" in (r.json().get("capabilities") or [])
    except (httpx.HTTPError, ValueError):
        return False
