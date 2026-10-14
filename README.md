<div align="center">

# 🛰️ AON : Self-Contained Obliterated AI

[![Live](https://img.shields.io/badge/live-aon.vulkron.uk-00e5ff?style=for-the-badge&logo=cloudflare&logoColor=white)](https://aon.vulkron.uk)
[![YouTube demo](https://img.shields.io/badge/demo-YouTube-FF0000?style=for-the-badge&logo=youtube&logoColor=white)](https://www.youtube.com/watch?v=wLiVhkLOd-8)
[![PyPI](https://img.shields.io/pypi/v/aon-agent?style=for-the-badge&logo=pypi&logoColor=white&label=PyPI&color=3775A9)](https://pypi.org/project/aon-agent/)
[![MCP server](https://img.shields.io/badge/MCP-server-6E56CF?style=for-the-badge&logo=modelcontextprotocol&logoColor=white)](#-use-aon-from-other-ai-apps-mcp)
[![License: MIT](https://img.shields.io/badge/license-MIT-22c55e?style=for-the-badge)](LICENSE)
[![Android APK](https://img.shields.io/badge/Android-download%20APK-3DDC84?style=for-the-badge&logo=android&logoColor=white)](https://github.com/leo007-htun/AON/releases/latest/download/AON.apk)

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.142-009688?logo=fastapi&logoColor=white)
![Uvicorn](https://img.shields.io/badge/Uvicorn-0.54-499848?logo=gunicorn&logoColor=white)
![httpx](https://img.shields.io/badge/httpx-0.28-2D7FF9)
![WebSockets](https://img.shields.io/badge/WebSockets-17.2-010101?logo=socketdotio&logoColor=white)
[![MCP](https://img.shields.io/badge/MCP-1.30-6E56CF)](https://modelcontextprotocol.io)
![pypdf](https://img.shields.io/badge/pypdf-5.1-E34F26)
![SQLite](https://img.shields.io/badge/SQLite-FTS5%20%2B%20vectors-003B57?logo=sqlite&logoColor=white)

![vLLM](https://img.shields.io/badge/vLLM-0.31-30A2FF)
![Ollama](https://img.shields.io/badge/Ollama-local%20models-000000?logo=ollama&logoColor=white)
![nomic-embed-text](https://img.shields.io/badge/embeddings-nomic--embed--text-5B5BD6)
![MapLibre](https://img.shields.io/badge/MapLibre-5.22-396CB2?logo=maplibre&logoColor=white)
![Cloudflare](https://img.shields.io/badge/Cloudflare-Tunnel%20%2B%20Access-F38020?logo=cloudflare&logoColor=white)
![pytest](https://img.shields.io/badge/tests-pytest-0A9EDC?logo=pytest&logoColor=white)

**[📦 Install](#-install) · [🔌 MCP server](#-use-aon-from-other-ai-apps-mcp) · [📱 Android](#-android-app) · [🔑 Live instance](#-trying-the-live-instance) · [🎬 Demo](#-demo)**

| 💬 Chat | 🗺️ Maps |
|:---:|:---:|
| ![AON chat UI](docs/screenshot.png) | ![AON route on the 3D globe map](docs/map.png) |

</div>

AON is a self-hosted chat agent that runs on your own hardware with local models. It answers
questions, runs tools in a workspace (files, shell commands, web fetch and search), remembers you
across chats, searches your own documents, and can be extended with skills and MCP servers. It
has a web UI, an Android app and an optional Telegram bot, and it is an
[MCP server](#-use-aon-from-other-ai-apps-mcp) itself, so Claude Desktop, Claude Code and Cursor can use it too.

> 🔒 Nothing is sent to a cloud LLM: chat runs on [Ollama](https://ollama.com) or
> [vLLM](https://docs.vllm.ai), embeddings on Ollama.

## 💡 What makes AON different

Memory and RAG are table stakes now. What AON adds:

- 🔓 **Obliterated and fully yours.** It runs an uncensored ("obliterated") 27B model on a single
  consumer GPU (RTX 4090), with no cloud, no API bills and no provider filtering or logging.
  Your chats, memory and documents never leave the box.
- 🧬 **It extends itself.** Paste a link and AON works out what it is: a GGUF model it can pull
  (it checks the VRAM budget and tool support first), an MCP server it can connect, a skill repo
  it can import, or docs it can learn from. It can also draft a new skill from any web page and
  test it with evals.
- 👥 **A private AI for your whole crew, from one GPU.** The chat model is served by vLLM, so
  several people use it at once. Access goes through Cloudflare Access email PIN, and every user
  is approved by hand.
- 🛡️ **Honest by design.** Shell commands and file writes wait for your approval. A reply that
  claims "done" without a tool having run is not trusted, so the real loop runs instead. Secrets
  such as tokens and keys are never saved to memory.
- 🗺️ **Grounded, not guessed.** Places and routes come from OpenStreetMap geocoding and routing,
  drawn on a 3D globe. Distances and times are computed by the routing service, not written by
  the model.
- 🔌 **Plugs into the AI apps you already use.** `aon mcp` makes AON an [MCP](#-use-aon-from-other-ai-apps-mcp) server:
  Claude Desktop, Claude Code or Cursor can hand it tasks and search its memory and your
  documents, while your data stays on your machine.
- 📱 **Everywhere you are.** The web UI, the Android app and an optional Telegram bot all reach the
  same agent on your server.

## 📱 Android app

[**Download AON.apk**](https://github.com/leo007-htun/AON/releases/latest/download/AON.apk) (260 KB, Android 7.0 or newer).

1. Open the link on your phone and download the APK.
2. Allow installs from your browser when Android asks (**Install unknown apps**), then tap **Install**.
3. Open AON and sign in with your approved email address (see
   [Trying the live instance](#-trying-the-live-instance)).

The app opens <https://aon.vulkron.uk> by default. To use your own server, press **Back** in the
app and choose **Change server**, for example `http://192.168.1.84:8700` on your home network.
The APK is signed (APK Signature Scheme v2, certificate `CN=AON, O=Vulkron, C=GB`); its SHA-256 is
listed on the [release page](https://github.com/leo007-htun/AON/releases/latest).

## 🎬 Demo

<div align="center">

[![Watch the AON demo on YouTube](https://img.youtube.com/vi/wLiVhkLOd-8/maxresdefault.jpg)](https://www.youtube.com/watch?v=wLiVhkLOd-8)

▶️ *Click the image to watch on YouTube*

</div>

## 🔑 Trying the live instance

Access to the live instance is by approval only:

1. 📝 **Request access.** [Open an issue](https://github.com/leo007-htun/AON/issues/new?title=Access%20request)
   titled *Access request*, saying who you are and what you would like to use AON for. Please do
   **not** post your email address in the issue; the maintainer will reply and tell you how to send it.
2. ⏳ **Wait for approval.** Requests are reviewed by hand. Once yours is approved, your email address
   is added to the allowed list and the issue is closed with a note.
3. ✅ **Sign in.** Open <https://aon.vulkron.uk>, enter the approved email address, and type the
   one-time PIN that is emailed to you. The PIN sign-in is handled by Cloudflare Access.

Access can be withdrawn at any time. The instance runs on private hardware with limited capacity:
please do not use it for anything illegal, for automated or bulk traffic, or to process other
people's personal data, and expect a short wait when several people are using it.

## ✨ Features

- 🛠️ **Agent loop with tools**: read, write and edit files, run shell commands, fetch and search the
  web, show maps, remember facts. Commands and file writes ask for your approval first.
- 🧠 **Long memory**
  - **Episodic memory**: facts about you and your work, stored and searched by meaning; a
    duplicate or an updated fact is merged by the model.
  - **Chat recall**: older turns of a chat (and of your other chats) are found by meaning and by
    exact strings (IPs, codes, numbers) and added to the request.
  - **Rolling summary** of each chat, plus a numbered outline of everything you asked, so
    "what did I ask first?" works in long chats.
- 📚 **Document library (RAG)**: add text, code, Markdown, HTML, Word (.docx) and PDF files in the
  Documents panel or by attaching them in chat. They are chunked, embedded and indexed (SQLite
  FTS5 + vectors); the relevant passages are added to answers, with the document name.
- 📏 **Context budget**: every request is fitted to the model's window using the server's exact token
  count (vLLM), so long chats never overflow.
- 🧩 **Skills**: reusable procedures in the Agent Skills (`SKILL.md`) format. Import from a zip, a
  GitHub URL or pasted text; draft one from a web page; run evals.
- 🔌 **MCP, both ways**: AON connects to stdio or HTTP MCP servers (browse the
  [Glama MCP directory](https://glama.ai/mcp/servers) in the Hub), and `aon mcp` makes AON an
  MCP server for other apps ([setup](#-use-aon-from-other-ai-apps-mcp)).
- 📎 **Attachments**: files and photos in chat; images go to vision models.
- 🗺️ **Maps**: places and routes from OpenStreetMap (Nominatim, OSRM), shown with MapLibre.
- 👥 **Multi-user serving**: put the chat model on vLLM to serve several users at once.

## 📋 Requirements

- Linux (runs on Ubuntu), Python 3.11 or newer (3.12 in use).
- [Ollama](https://ollama.com) with:
  - an embedding model: `ollama pull nomic-embed-text`
  - a chat model with tool support, for example `ollama pull qwen3:14b`, unless chat runs on vLLM
- Optional: a GPU server running vLLM (see [`vllm/README.md`](vllm/README.md)).
- Optional: Node.js 20 for `npx`-based MCP servers (`bin/npx` is a wrapper for a bundled copy).

## 📦 Install

```bash
pipx install aon-agent            # or: pip install aon-agent
aon init                          # creates ~/.aon with a starter config.toml
```

`aon init` takes `--ollama URL`, `--model NAME`, `--bind HOST:PORT` and `--home DIR`. Then pull the
models it names:

```bash
ollama pull nomic-embed-text
ollama pull qwen3:14b             # or the chat model you chose with --model
```

**From source** (for development):

```bash
git clone https://github.com/leo007-htun/AON.git && cd AON
python3 -m venv venv && venv/bin/pip install -e ".[test]"
venv/bin/aon init
```

Everything AON keeps (config, chats, memory, documents, skills) lives in its **home folder**:
`--home DIR`, else `$AON_HOME`, else `~/.aon`.

## ⚙️ Configure (`config.toml`)

`aon init` writes this file into the home folder; edit it to taste.

| Key | Meaning | Example |
|---|---|---|
| `ollama_base_url` | Ollama's OpenAI-compatible endpoint (embeddings, and chat unless `chat_base_url` is set) | `"http://localhost:11434/v1"` |
| `default_model` | Model used when the UI does not pick one | `"qwen3:14b"` |
| `workdir` | The agent's workspace; chats and documents are stored here | `"~/app/workspace"` |
| `bind` | Address and port of the web UI | `"0.0.0.0:8700"` |
| `max_steps` | Tool calls allowed per request | `40` |
| `command_timeout` | Seconds before a shell command is stopped | `120` |
| `always_ask` | Extra tools that always need approval | `[]` |
| `embed_model` | Embedding model on Ollama | `"nomic-embed-text"` |
| `context_window` | The chat model's context length, in tokens | `16384` |
| `reply_reserve` | Tokens kept free for thinking and the answer | `6144` |
| `history_tokens` | Recent chat turns sent word for word | `5000` |
| `cross_chat_recall` | Also recall relevant turns from your other chats | `true` |
| `chat_base_url` | Optional separate chat backend (vLLM) | `"http://gpu-box:8001/v1"` |
| `chat_model` | The model name served there; only this model goes to `chat_base_url` | `"aon"` |
| `chat_temperature`, `chat_top_p`, `chat_repetition_penalty` | Sampling for the chat backend | `0.2`, unset, `1.15` |
| `chat_vision` | The chat backend model accepts images | `true` |
| `telegram_bot_token`, `telegram_allowed_chat_ids` | Optional Telegram bot | `""`, `[]` |

Keep `context_window` equal to vLLM's `--max-model-len` (or the Ollama model's context).

## 🚀 Run

```bash
aon serve                         # web UI on the bind address from config.toml
aon serve --port 9000             # or override host/port
aon --help
```

Then open `http://<bind address>` in a browser.

### 🌙 In the background

```bash
setsid nohup aon serve >> ~/.aon/aon.log 2>&1 < /dev/null &
pgrep -af '[a]on serve'           # is it running?
tail -f ~/.aon/aon.log            # its log
pkill -f '[a]on serve'            # stop it
```

### 🔁 As a service (starts at boot, restarts on failure)

```bash
sudo tee /etc/systemd/system/aon.service >/dev/null <<'EOF'
[Unit]
Description=AON agent
After=network-online.target
Wants=network-online.target

[Service]
User=ubuntu
Environment=AON_HOME=/home/ubuntu/.aon
ExecStart=/home/ubuntu/.local/bin/aon serve
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now aon
journalctl -u aon -f
```

Change `User`, `AON_HOME` and the `aon` path (`which aon`) to match your setup. Python code changes need a restart;
`static/index.html` is read on every request, so UI changes show on reload.

## 🔌 Use AON from other AI apps (MCP)

`aon mcp` runs AON as a [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server over
stdio, so [Claude Desktop](https://modelcontextprotocol.io/docs/develop/connect-local-servers),
[Claude Code](https://code.claude.com/docs/en/mcp), [Cursor](https://cursor.com/docs/mcp) and
other MCP clients can use it. It ships in the [`aon-agent`](https://pypi.org/project/aon-agent/)
package; the code is [`src/aon/mcp_server.py`](src/aon/mcp_server.py).

**Quick start**

```bash
pipx install aon-agent
aon init          # once: creates ~/.aon
aon mcp           # what your MCP client runs (you don't start it by hand)
```

**Tools**

| Tool | What it does |
|---|---|
| `ask_aon` | Hand AON a task or question; it answers with its own model, memory, documents and tools. Use `chat` to continue a conversation. |
| `search_memory` | Search AON's long-term memory about you (read-only) |
| `remember` | Save a fact to AON's memory |
| `search_documents` | Search your document library (read-only) |
| `list_documents` | List the library (read-only) |
| `add_document` | Add a text document to the library |

Shell commands and file writes are always refused over MCP, because there is no one to approve them there.

**Connect a client.** Claude Desktop and Cursor both take the same JSON:

- **[Claude Desktop](https://modelcontextprotocol.io/docs/develop/connect-local-servers)**: Settings → Developer → Edit Config (`claude_desktop_config.json`), then restart Claude Desktop.
- **[Cursor](https://cursor.com/docs/mcp)**: `~/.cursor/mcp.json`, or `.cursor/mcp.json` in a project.

```json
{
  "mcpServers": {
    "aon": { "command": "aon", "args": ["mcp"] }
  }
}
```

**[Claude Code](https://code.claude.com/docs/en/mcp):**

```bash
claude mcp add aon -- aon mcp
```

Add `"--home", "/path/to/home"` to `args` to use a different home folder. If the client cannot find
`aon`, give the full path from `which aon`.

## ⚡ Chat on vLLM (several users at once)

Ollama answers one request at a time for some models. To serve several users, run the chat
model on vLLM and point AON at it:

```toml
chat_base_url = "http://<gpu-box>:8001/v1"
chat_model = "aon"
default_model = "aon"
context_window = 16384
```

[`vllm/`](vllm/) has the systemd unit, the chat template and the exact package versions used.
Measured on one RTX 4090 (24 GB) with a 27B 4-bit model: 16K context serves 2–3 full-length
requests at once and the rest queue; 8K serves 4.

## 🌐 Remote access

AON has no login of its own. Keep `bind` on your LAN and, to reach it from outside, put it behind
an authenticating proxy, for example a Cloudflare Tunnel with Cloudflare Access (email PIN).
Geolocation in the map needs HTTPS.

## 🧪 Tests

```bash
pip install -e ".[test]"
pytest -q
```

## 🗂️ Project layout

| Path | What it is |
|---|---|
| `src/aon/cli.py` | The `aon` command: `init`, `serve`, `mcp` |
| `src/aon/main.py` | Builds AON from `config.toml` (shared by `serve` and `mcp`) |
| `src/aon/mcp_server.py` | AON as an MCP server |
| `src/aon/web.py` | FastAPI app: REST API and the chat WebSocket |
| `src/aon/engine.py` | The agent loop (tool calls, approval, loop guard) |
| `src/aon/router.py` | Planner: answer directly, ask, or plan tool steps |
| `src/aon/llm_client.py`, `backends.py` | Model calls; Ollama or vLLM per model |
| `src/aon/context.py` | Fitting requests into the context window |
| `src/aon/recall.py`, `summary.py` | Chat recall, rolling summary, outline |
| `src/aon/memory*.py` | Episodic memory, extraction, consolidation |
| `src/aon/docs.py` | Document library (RAG) |
| `src/aon/skills.py`, `skill_*.py` | Skills: store, import, authoring, evals |
| `src/aon/mcp_client.py`, `mcp_tools.py`, `hub.py` | MCP servers and the Glama hub |
| `src/aon/tools/` | Built-in tools (files, shell, web, map) |
| `src/aon/static/` | Web UI (`index.html`), icons, fonts, map library |
| `src/aon/default_skills/` | Skills copied into a new home by `aon init` |
| `tests/` | Test suite |
| `specs/` | Design notes |
| `vllm/` | vLLM server setup |

## 💾 Data

AON keeps its data in its home folder: `workspace/app.db` (chats), `workspace/docs.db` (document
library), `memory/episodes.db` (memory) and `workspace/uploads/` (attachments). This repository
includes a snapshot of them from 2026-10-14. A fresh install creates empty ones; to start clean,
delete them before the first run. The Glama API key is kept in `.glama_key`, which is git-ignored.

## 📄 License

AON is released under the [MIT License](LICENSE). © 2026 Sithu Htun ([@leo007-htun](https://github.com/leo007-htun)).

Bundled third-party files keep their own licenses, included next to them:
[CybercoreCSS](static/lib/cybercore.LICENSE) (MIT),
[MapLibre GL JS](static/lib/maplibre-gl.LICENSE) (BSD-3-Clause),
highlight.js (BSD-3-Clause), and the Exo 2, JetBrains Mono, Orbitron and Rajdhani fonts
([SIL Open Font License](static/fonts/)).
