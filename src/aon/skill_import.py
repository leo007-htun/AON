"""Import Agent Skills from a .zip, a GitHub repo URL, or a pasted SKILL.md.

Every source becomes zip bytes, then: scan_zip() previews the skills inside (any folder
holding a SKILL.md), install_zip() copies the chosen ones into the SkillStore.
Archives are untrusted: absolute paths, `..`, symlinks and oversize archives are refused.
"""
from __future__ import annotations
import io
import posixpath
import re
import shutil
import stat
import zipfile
from aon.skills import SkillStore, parse_frontmatter, validate_description
from aon.tools.paths import resolve_in_root

MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_FILES = 5000
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SCRIPT_EXT = {".sh", ".bash", ".py", ".js", ".mjs", ".cjs", ".ts", ".ps1", ".bat", ".cmd", ".rb", ".pl"}


class ImportError_(ValueError):
    """The archive or source can't be imported (bad zip, unsafe path, too large, bad URL)."""


def _entries(z: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = [i for i in z.infolist() if not i.is_dir()]
    if len(infos) > MAX_FILES:
        raise ImportError_(f"archive has {len(infos)} files (max {MAX_FILES})")
    if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
        raise ImportError_(f"archive is larger than {MAX_TOTAL_BYTES // (1024 * 1024)} MB unpacked")
    out = []
    for i in infos:
        n = i.filename.replace("\\", "/")
        if n.startswith("/") or re.match(r"^[A-Za-z]:", n) or ".." in n.split("/"):
            raise ImportError_(f"unsafe path in archive: {i.filename!r}")
        if stat.S_ISLNK(i.external_attr >> 16):
            raise ImportError_(f"symlink in archive: {i.filename!r}")
        if n.split("/")[0] == "__MACOSX":
            continue
        out.append(i)
    return out


def _common_top(names: list[str]) -> str:
    tops = {n.split("/")[0] for n in names}
    if len(tops) == 1 and all("/" in n for n in names):
        return tops.pop() + "/"
    return ""


def _is_script(rel: str, info: zipfile.ZipInfo) -> bool:
    return (rel.startswith("scripts/") or posixpath.splitext(rel)[1].lower() in _SCRIPT_EXT
            or bool((info.external_attr >> 16) & 0o111))


def _scan(data: bytes):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ImportError_("not a valid .zip file")
    infos = _entries(z)
    names = [i.filename.replace("\\", "/") for i in infos]
    top = _common_top(names)
    dirs = sorted({posixpath.dirname(n) for n in names if posixpath.basename(n) == "SKILL.md"})
    # a SKILL.md inside another skill (e.g. an example) belongs to the outer skill
    dirs = [d for d in dirs if not any(o != d and (o == "" or d.startswith(o + "/")) for o in dirs)]
    skills, seen = [], set()
    for d in dirs:
        prefix = d + "/" if d else ""
        members = [(n[len(prefix):], i) for n, i in zip(names, infos) if n.startswith(prefix)]
        meta, _ = parse_frontmatter(z.read(prefix + "SKILL.md").decode("utf-8", "replace"))
        desc = meta.get("description", "")
        name = meta.get("name", "")
        if not _NAME_RE.match(name):
            base = posixpath.basename(d)
            name = base if _NAME_RE.match(base) else ""
        error = None
        if not name:
            error = "no valid skill name (frontmatter name or folder name)"
        elif name in seen:
            error = f"duplicate skill name {name!r}"
        elif not desc:
            error = "SKILL.md frontmatter has no description"
        else:
            error = validate_description(desc)
        seen.add(name)
        rels = sorted(r for r, _ in members)
        skills.append({"name": name, "description": desc, "error": error,
                       "path": (prefix[len(top):] if prefix.startswith(top) else prefix).rstrip("/"),
                       "files": rels,
                       "scripts": sorted(r for r, i in members if _is_script(r, i)),
                       "_members": members})
    skills.sort(key=lambda s: s["name"])
    return z, skills


def scan_zip(data: bytes) -> list[dict]:
    """Preview: the skills found in the archive (name, description, files, scripts, error, path)."""
    _, skills = _scan(data)
    return [{k: v for k, v in s.items() if k != "_members"} for s in skills]


def install_zip(store: SkillStore, data: bytes, names: list[str] | None = None,
                overwrite: bool = False) -> dict:
    """Install the selected skills (all valid ones if names is None) into the store."""
    z, skills = _scan(data)
    installed, skipped = [], []
    for s in skills:
        if names is not None and s["name"] not in names:
            continue
        if s["error"]:
            skipped.append({"name": s["name"], "reason": s["error"]}); continue
        dest = store._safe(s["name"])
        if dest.exists():
            if not overwrite:
                skipped.append({"name": s["name"], "reason": "already installed"}); continue
            shutil.rmtree(dest)
        for rel, info in s["_members"]:
            target = resolve_in_root(dest, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
            mode = (info.external_attr >> 16) & 0o777
            if mode & 0o111:
                target.chmod(0o755)
        installed.append(s["name"])
    return {"installed": installed, "skipped": skipped}


def zip_from_skill_md(text: str) -> bytes:
    """Wrap a pasted SKILL.md as a one-skill archive (its name becomes the folder).

    Missing frontmatter is filled in: name from the first `# Heading`, description from
    the first plain line of the body."""
    meta, body = parse_frontmatter(text)
    name = meta.get("name", "")
    desc = meta.get("description", "")
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    title = ""
    if not _NAME_RE.match(name):
        title = next((ln for ln in lines if ln.startswith("#")), "")
        if not title and lines and len(lines[0]) <= 60 and not lines[0].endswith((".", ":")):
            title = lines[0]              # copied from a rendered page: first short line is the title
        name = re.sub(r"[^a-z0-9]+", "-", title.lstrip("#").strip().lower()).strip("-")[:64]
        if not _NAME_RE.match(name):
            raise ImportError_("pasted SKILL.md needs a `name:` in its frontmatter, a `# Heading`, "
                               "or a short title on the first line")
    if not desc:
        desc = next((ln for ln in lines if ln != title and not ln.startswith(("#", "-", "*", "```"))
                     and not re.match(r"^\d+\.", ln)), "")
    if meta.get("name") != name or not meta.get("description"):
        extra = "".join(f"{k}: {v}\n" for k, v in meta.items() if k not in ("name", "description"))
        text = f"---\nname: {name}\ndescription: {desc}\n{extra}---\n\n{body.lstrip()}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{name}/SKILL.md", text)
    return buf.getvalue()


_GH_RE = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?"
                    r"(?:/tree/([^/]+)(?:/(.*?))?)?/?$")


def _gh_parts(url: str):
    m = _GH_RE.match(url.strip())
    if not m:
        raise ImportError_("only github.com repository URLs are supported "
                           "(https://github.com/<owner>/<repo>[/tree/<branch>/<path>])")
    return m.groups()


def github_zip_url(url: str) -> str:
    owner, repo, ref, _ = _gh_parts(url)
    return f"https://api.github.com/repos/{owner}/{repo}/zipball" + (f"/{ref}" if ref else "")


def github_subpath(url: str) -> str:
    """The folder inside the repo the URL points at ('' for the whole repo)."""
    return (_gh_parts(url)[3] or "").strip("/")


async def fetch_github_zip(url: str) -> bytes:
    import httpx
    api = github_zip_url(url)
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        async with client.stream("GET", api, headers={"Accept": "application/vnd.github+json"}) as r:
            if r.status_code != 200:
                raise ImportError_(f"GitHub download failed: HTTP {r.status_code} for {api}")
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) > MAX_DOWNLOAD_BYTES:
                    raise ImportError_("repository archive is too large")
    return bytes(buf)


async def import_from_github(store: SkillStore, url: str, names: list[str] | None = None,
                             overwrite: bool = False) -> dict:
    """Download a GitHub repo (or one folder of it) and install the skills found there."""
    data = await fetch_github_zip(url)
    sub = github_subpath(url)
    found = [s["name"] for s in scan_zip(data)
             if not sub or s["path"] == sub or s["path"].startswith(sub + "/")]
    chosen = found if names is None else [n for n in names if n in found]
    return install_zip(store, data, names=chosen, overwrite=overwrite)
