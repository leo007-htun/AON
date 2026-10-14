"""The `aon` command.

    aon init    create a home folder with a starter config.toml
    aon serve   run the web UI
    aon mcp     run AON as an MCP server over stdio (for Claude Desktop, Claude Code, Cursor, ...)

Everything AON keeps (config.toml, chats, memory, documents, skills) lives in one home folder:
--home, else $AON_HOME, else ~/.aon. AON runs inside that folder, so an existing install such as
~/app on a server is a valid home as it is.
"""
from __future__ import annotations
import argparse
import os
import shutil
import sys
from pathlib import Path
from aon import __version__

DEFAULT_HOME = "~/.aon"
CONFIG = "config.toml"

CONFIG_TEMPLATE = """\
# AON configuration: https://github.com/leo007-htun/AON#%EF%B8%8F-configure-configtoml
ollama_base_url = "{ollama}"     # Ollama's OpenAI-compatible endpoint (chat + embeddings)
default_model = "{model}"         # a chat model with tool support
workdir = "{workdir}"
bind = "{bind}"                   # address:port of the web UI
always_ask = []
max_steps = 40
command_timeout = 120
embed_model = "nomic-embed-text"
context_window = 16384           # the chat model's context length
reply_reserve = 6144
history_tokens = 5000

# Optional: chat on a vLLM server for several users at once
# chat_base_url = "http://gpu-box:8001/v1"
# chat_model = "aon"
"""


def resolve_home(arg: str | None) -> Path:
    return Path(arg or os.environ.get("AON_HOME") or DEFAULT_HOME).expanduser().resolve()


def init_home(home: Path, ollama: str, model: str, bind: str, force: bool = False) -> list[str]:
    """Create the home folder; existing files are kept unless force. Returns what was written."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    created = []
    for d in ("workspace", "memory", "skills"):
        (home / d).mkdir(exist_ok=True)
    cfg = home / CONFIG
    if force or not cfg.exists():
        cfg.write_text(CONFIG_TEMPLATE.format(ollama=ollama, model=model, bind=bind,
                                              workdir=(home / "workspace").as_posix()), encoding="utf-8")
        created.append(CONFIG)
    if not (home / "mcp.json").exists():
        (home / "mcp.json").write_text("[]\n", encoding="utf-8")
        created.append("mcp.json")
    bundled = Path(__file__).resolve().parent / "default_skills"
    for skill in sorted(bundled.iterdir()) if bundled.is_dir() else []:
        target = home / "skills" / skill.name
        if skill.is_dir() and not target.exists():
            shutil.copytree(skill, target)
            created.append(f"skills/{skill.name}")
    return created


def _enter(home: Path) -> bool:
    if not (home / CONFIG).exists():
        print(f"No {CONFIG} in {home}. Create one first:\n  aon init --home {home}", file=sys.stderr)
        return False
    os.chdir(home)          # AON's data paths (memory/, skills/, mcp.json, ...) are relative to home
    return True


def _cmd_init(a) -> int:
    home = resolve_home(a.home)
    created = init_home(home, a.ollama, a.model, a.bind, force=a.force)
    print(f"AON home: {home}")
    for f in created:
        print(f"  created {f}")
    if CONFIG not in created:
        print(f"  kept the existing {CONFIG} (use --force to replace it)")
    print("\nNext:\n"
          f"  ollama pull nomic-embed-text && ollama pull {a.model}\n"
          f"  aon serve{'' if a.home is None else ' --home ' + str(home)}     # web UI on http://{a.bind}\n"
          f"  aon mcp{'' if a.home is None else ' --home ' + str(home)}       # MCP server (stdio)")
    return 0


def _cmd_serve(a) -> int:
    home = resolve_home(a.home)
    if not _enter(home):
        return 2
    import uvicorn
    from aon.config import load_config
    from aon.main import build_app_from_config
    cfg = load_config(CONFIG)
    host, _, port = cfg.bind.partition(":")
    app = build_app_from_config(CONFIG)
    # attachments arrive base64-encoded over the websocket (40 MB raw max, ~54 MB encoded)
    uvicorn.run(app, host=a.host or host or "127.0.0.1", port=a.port or int(port or 8700),
                ws_max_size=64 * 1024 * 1024)
    return 0


def _cmd_mcp(a) -> int:
    home = resolve_home(a.home)
    if not _enter(home):
        return 2
    from aon.mcp_server import run_stdio
    run_stdio(CONFIG)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="aon", description="AON, a self-hosted AI agent.")
    p.add_argument("--version", action="version", version=f"aon {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    home_help = f"AON's home folder (default: $AON_HOME or {DEFAULT_HOME})"

    i = sub.add_parser("init", help="create a home folder with a starter config")
    i.add_argument("--home", help=home_help)
    i.add_argument("--ollama", default="http://localhost:11434/v1", help="Ollama's /v1 URL")
    i.add_argument("--model", default="qwen3:14b", help="chat model with tool support")
    i.add_argument("--bind", default="127.0.0.1:8700", help="address:port for the web UI")
    i.add_argument("--force", action="store_true", help="replace an existing config.toml")
    i.set_defaults(fn=_cmd_init)

    s = sub.add_parser("serve", help="run the web UI")
    s.add_argument("--home", help=home_help)
    s.add_argument("--host", help="override the host from config.toml")
    s.add_argument("--port", type=int, help="override the port from config.toml")
    s.set_defaults(fn=_cmd_serve)

    m = sub.add_parser("mcp", help="run AON as an MCP server over stdio")
    m.add_argument("--home", help=home_help)
    m.set_defaults(fn=_cmd_mcp)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
