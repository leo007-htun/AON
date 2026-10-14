# 🛰️ AON : Self-Contained Obliterated AI

![AON](https://raw.githubusercontent.com/leo007-htun/AON/main/docs/aon-logo-animated.svg)

AON is a self-hosted AI agent that runs on your own hardware with local models. It answers questions, runs tools (files, shell, web search, maps), remembers you across chats, reads your documents, and extends itself with new models, skills and MCP servers. Use it from the web, the Android app, Telegram, or from Claude Desktop, Claude Code and Cursor through the MCP server.

> Important: Nothing leaves your box. Chat runs on vLLM or Ollama on your own GPU, embeddings on Ollama. No cloud LLM, no API bills, no provider filtering or logging.

## Quick start

```bash
pipx install aon-agent
aon init
aon serve
```

> Tip: You need Ollama with `nomic-embed-text` and a chat model that supports tools, such as `qwen3:14b`. Then try `aon mcp` to plug AON into Claude Desktop.

## Why AON

Memory and RAG are table stakes now. What AON adds:

- 🔓 Obliterated and fully yours. An uncensored model on a single consumer GPU. Your chats, memory and documents never leave the box.
- 🧬 It extends itself. Paste a link and AON works out what it is: a GGUF model to pull, an MCP server to connect, a skill repo to import, or docs to learn from.
- 🔌 It plugs into the AI apps you already use. `aon mcp` turns AON into an MCP server for Claude Desktop, Claude Code and Cursor, so they can hand it tasks and search memory and documents.
- 👥 A private AI for your whole crew, from one GPU. One user can share it among a team behind Cloudflare Access with every user approved by hand.
- 🛡️ Honest by design. Shell commands and file writes wait for your approval. A reply that claims "done" without a tool having run is not trusted. Secrets are never saved to memory.
- 🗺️ Grounded, not guessed. Places and routes come from OpenStreetMap routing on a 3D globe; distances and times are computed, not written by the model.

## MCP server

Use AON inside Claude Desktop, Claude Code, Cursor and any other MCP client.

`aon mcp` runs AON as a Model Context Protocol server. It ships in the `aon-agent` package and starts via your MCP client.

| Tool | What it does | Access |
|---|---|:---:|
| `ask_aon` | Hand AON a task or question; it answers with its own model, memory, documents and tools. Pass `chat` to continue a conversation. | ✍️ write |
| `search_memory` | Search AON's long-term memory about you. | 👀 read |
| `remember` | Save a fact to AON's memory. | ✍️ write |
| `search_documents` | Search your document library by meaning and exact words. | 👀 read |
| `list_documents` | List the library. | 👀 read |
| `add_document` | Add a text document to the library. | ✍️ write |

> Shell commands and file writes are always refused over MCP: there is nobody to approve them inside another app.

Claude Desktop and Cursor config example:

```json
{
  "mcpServers": {
    "aon": { "command": "aon", "args": ["mcp"] }
  }
}
```

Claude Code:

```bash
claude mcp add aon -- aon mcp
```

Add `--home /path/to/home` if you want a different home folder.

## Features

- 🛠️ Agent with tools: read, write and edit files, run shell commands, search and fetch the web, show maps. Commands and file writes ask for approval.
- 🧠 Long memory: facts about you, searched by meaning and merged when they change, plus recall of older turns and rolling summary/outline features.
- 📚 Document library (RAG): text, code, Markdown, HTML, Word and PDF from chat attachments or the Documents panel. Hybrid search uses SQLite FTS5 and vectors.
- 📏 Context budget: every request is fitted to the model's context window using the server's exact token count.
- 🧩 Skills: import skills from zip, GitHub URL or pasted text; draft one from a web page; run evals.
- 🔌 MCP, both ways: connects to stdio and HTTP MCP servers and acts as an MCP server itself.
- 📎 Attachments: files and photos in chat; images go to vision models.
- 🗺️ Maps: places and routes from OpenStreetMap on a 3D globe.
- 👥 Multi-user: chat on vLLM to serve several people at once with Cloudflare Access approval.
- 📱 Everywhere: web UI, Android app and optional Telegram bot.

## Android app

Download the APK from GitHub releases: https://github.com/leo007-htun/AON/releases/latest/download/AON.apk

1. Open the link on your phone and download the APK.
2. Allow installs from your browser when Android asks, then tap Install.
3. Open AON and sign in with your approved email address.

The app opens https://aon.vulkron.uk by default, or you can point it at your own AON server such as `http://192.168.1.84:8700`.

## Installation and configuration

Requirements:

- Linux (runs on Ubuntu), Python 3.11 or newer.
- Ollama with an embedding model and a chat model with tool support, unless chat runs on vLLM.
- Optional: a GPU server running vLLM.
- Optional: Node.js 20 for `npx`-based MCP servers.

Install:

```bash
pipx install aon-agent
# or: pip install aon-agent
aon init
```

Run:

```bash
aon serve --home /path/to/home
```

Everything AON keeps (config, chats, memory, documents, skills) lives in its home folder (`--home DIR`, `$AON_HOME`, or `~/.aon`).

## License

AON is licensed under the MIT License. See the full license here: https://github.com/leo007-htun/AON/blob/main/LICENSE
