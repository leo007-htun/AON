from pathlib import Path

PATH_PARAM = {"type": "string",
              "description": "Path relative to the workspace root: '.' is the root itself, 'src/app.py' a file in it. "
                             "Do not guess absolute paths; anything outside the workspace is refused."}

def resolve_in_root(root: Path, rel: str) -> Path:
    root = root.resolve()
    candidate = (root / rel).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError(f"path {rel!r} is outside the workdir; use a path relative to the workspace, e.g. '.'")
    return candidate
