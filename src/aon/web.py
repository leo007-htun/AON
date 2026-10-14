import asyncio
import json
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from aon.config import Config
from aon.persistence import Database
from aon.llm_client import LLMClient
from aon.approval_gate import QueueGate, Decision
from aon.prompts import load_system_prompt, save_system_prompt
from aon.memory import MemoryStore, EpisodicMemory
from aon.memory_extract import maybe_extract
from aon.context import fit_history
from aon.summary import update_summary, summary_background, outline_background
from aon.recall import index_turns, recall_background, index_all, other_chats_background, backfill
from aon.backends import make_chat_llm, chat_base_url, model_is_chat
from aon.memory_semantic import consolidate
from aon.skills import SkillStore
from aon.geo import parse_map_command, build_map, default_geo, choices_marker
from aon.attachments import AttachmentError, save_attachments, attachment_note, image_parts, supports_vision

_STATIC = Path(__file__).resolve().parent / "static"     # shipped inside the package

def model_error_text(exc: Exception, model: str) -> str:
    """What the user sees when a model call fails (the run would otherwise end with no reply)."""
    import httpx
    if isinstance(exc, httpx.HTTPStatusError):
        r = exc.response
        try:
            body = r.json()
            err = body.get("error") if isinstance(body, dict) else None
            detail = (err.get("message") if isinstance(err, dict) else err) or ""
        except ValueError:
            detail = ""
        detail = str(detail or r.text or "").strip()[:500]
        text = f"The model ({model}) rejected the request (HTTP {r.status_code}): {detail}"
        if "context size" in detail or "context length" in detail:
            text += "\nThis chat is now longer than the model can read at once. Start a new chat to continue."
        elif "does not support tools" in detail:
            text += "\nPick a model with tool support in the model menu."
        return text
    if isinstance(exc, httpx.TransportError):
        return f"Could not reach the model server for {model} ({type(exc).__name__}). Check that Ollama is running."
    return f"The run failed: {type(exc).__name__}: {str(exc)[:300]}"


async def _list_models(base_url: str, model: str) -> list[str]:
    return await LLMClient(base_url, model).list_models()

def create_app(cfg: Config, db: Database, make_agent, prompt_path=None, mem_path=None,
               skills_dir=None, eval_runner_factory=None, mcp_manager=None,
               vision_check=None, geo=None, hub=None, embedder=None, docs=None) -> FastAPI:
    vision_check = vision_check or supports_vision
    app = FastAPI()
    if _STATIC.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
    prompt_path = prompt_path or Path("system_prompt.txt")
    mem_path = mem_path or (Path("memory") / "episodes.db")
    skills_dir = skills_dir or Path("skills")
    app.state.cfg = cfg; app.state.db = db; app.state.make_agent = make_agent
    app.state.mcp = mcp_manager
    if embedder is not None:
        @app.on_event("startup")
        async def _index_startup():
            # old chats get vectors in the background, so recall across chats can find them
            async def run():
                try:
                    await backfill(db, embedder)
                except Exception:
                    pass
            app.state.backfill = asyncio.create_task(run())

    if mcp_manager is not None:
        @app.on_event("startup")
        async def _mcp_startup():
            try:
                await mcp_manager.connect_all()
            except Exception:
                pass

    def _runner():
        from aon.skill_evals import EvalRunner
        store = SkillStore(skills_dir)
        return (eval_runner_factory or (lambda cfg, store: EvalRunner(cfg, store)))(cfg, store)

    @app.get("/api/models")
    async def models():
        try:
            return {"models": await _list_models(chat_base_url(cfg), cfg.default_model)}
        except Exception as e:
            return JSONResponse({"error": str(e)})

    @app.post("/api/map")
    async def map_command(body: dict):
        # "/map <places> [3d|globe|2d] [orbit] [walk|drive|cycle|flight]": answered without the agent
        spec = parse_map_command((body or {}).get("text", ""))
        if spec is None:
            return JSONResponse({"error": "usage: /map <place> | /map <place>; <place> | /map <from> to <to> [walk|cycle|flight] [2d|3d|globe] [orbit]"})
        mr = await build_map(spec, geo or default_geo())
        return {"event": mr.event, "notices": mr.notices, "context": mr.context, "ask": mr.ask}

    @app.get("/api/system-prompt")
    async def get_prompt():
        return {"prompt": load_system_prompt(prompt_path)}

    @app.post("/api/system-prompt")
    async def set_prompt(body: dict):
        text = (body or {}).get("prompt", "")
        if not text.strip():
            return JSONResponse({"error": "prompt must not be empty"})
        save_system_prompt(prompt_path, text)
        return {"ok": True}

    def _mem():
        return EpisodicMemory(mem_path)

    @app.get("/api/memory")
    async def api_memory(all: int = 0):
        return {"episodes": _mem().all(include_inactive=bool(all))}

    @app.delete("/api/conversations/{convo}")
    async def api_conversation_delete(convo: str):
        # a chat deleted in the page is deleted here too, so recall never brings it back
        db.clear_conversation(convo); return {"ok": True}

    @app.delete("/api/memory")
    async def api_memory_clear():
        _mem().clear(); return {"ok": True}

    @app.delete("/api/memory/{eid}")
    async def api_memory_delete(eid: int):
        _mem().delete(eid); return {"ok": True}

    @app.post("/api/memory/{eid}/outdated")
    async def api_memory_outdated(eid: int):
        _mem().mark_outdated(eid); return {"ok": True}

    # --- document library (RAG): files added here are chunked, embedded and searched per request ---
    @app.get("/api/docs")
    async def api_docs():
        return {"documents": docs.list() if docs is not None else []}

    @app.post("/api/docs")
    async def api_docs_add(body: dict):
        import base64, binascii
        from aon.docs import ingest, DocError
        from aon.attachments import MAX_FILE_BYTES
        if docs is None:
            return JSONResponse({"error": "the document library is not enabled"})
        body = body or {}
        try:
            data = base64.b64decode(body.get("data") or "", validate=True)
        except (binascii.Error, ValueError):
            return JSONResponse({"error": "data is not valid base64"})
        if not data:
            return JSONResponse({"error": "the file is empty"})
        if len(data) > MAX_FILE_BYTES:
            return JSONResponse({"error": f"larger than {MAX_FILE_BYTES // (1024 * 1024)} MB"})
        try:
            return {"document": await ingest(docs, embedder, str(body.get("name") or "document"), data)}
        except DocError as e:
            return JSONResponse({"error": str(e)})

    @app.delete("/api/docs/{doc_id}")
    async def api_docs_delete(doc_id: int):
        return {"ok": docs.delete(doc_id) if docs is not None else False}

    @app.get("/api/skills")
    async def api_skills():
        from aon.composite_skills import CompositeSkills
        return {"skills": CompositeSkills(SkillStore(skills_dir), getattr(app.state, "mcp", None)).items()}

    # --- MCP Hub: the Glama directory, proxied so the API key never reaches the browser ---
    if hub is None:
        from aon.hub import GlamaHub
        hub = GlamaHub(Path(".glama_key"))

    async def _hub_call(coro):
        from aon.hub import HubError
        try:
            return await coro
        except HubError as e:
            return JSONResponse({"error": e.message, "code": e.code})

    @app.get("/api/hub/glama/status")
    async def hub_status():
        return {"configured": hub.configured()}

    @app.post("/api/hub/glama/key")
    async def hub_key(body: dict):
        key = str((body or {}).get("key", "")).strip()
        if not key or not await hub.check_key(key):
            return JSONResponse({"error": "Glama did not accept that API key."})
        hub.set_key(key)
        return {"ok": True}

    @app.get("/api/hub/mcp/servers")
    async def hub_servers(query: str = "", after: str | None = None):
        return await _hub_call(hub.servers(query=query, after=after))

    @app.get("/api/hub/mcp/servers/{namespace}/{slug}")
    async def hub_server(namespace: str, slug: str):
        return await _hub_call(hub.server(namespace, slug))

    @app.get("/api/hub/mcp/connectors")
    async def hub_connectors(query: str = "", after: str | None = None):
        return await _hub_call(hub.connectors(query=query, after=after))

    @app.get("/api/hub/mcp/connectors/{namespace}/{slug}")
    async def hub_connector(namespace: str, slug: str):
        return await _hub_call(hub.connector(namespace, slug))

    @app.get("/api/mcp")
    async def mcp_list():
        return {"servers": mcp_manager.status() if mcp_manager else []}

    @app.post("/api/mcp")
    async def mcp_add(body: dict):
        if not mcp_manager:
            return JSONResponse({"error": "mcp disabled"})
        from aon.mcp_client import McpServerCfg
        cfg2 = McpServerCfg(body["name"], body.get("command", ""), list(body.get("args", [])),
                            bool(body.get("enabled", True)), transport=body.get("transport", "stdio"),
                            url=body.get("url", ""), headers=dict(body.get("headers") or {}))
        mcp_manager.add_server(cfg2)
        try:
            await mcp_manager.connect(cfg2)
        except Exception:
            pass
        return {"ok": True}

    @app.delete("/api/mcp/{name}")
    async def mcp_del(name: str):
        if mcp_manager:
            await mcp_manager.disconnect(name)
            mcp_manager.remove_server(name)
        return {"ok": True}

    @app.post("/api/mcp/{name}/toggle")
    async def mcp_toggle(name: str, body: dict):
        if mcp_manager:
            on = bool((body or {}).get("enabled", True))
            mcp_manager.set_enabled(name, on)
            if on:
                for s in mcp_manager.list_servers():
                    if s.name == name:
                        await mcp_manager.connect(s)
            else:
                await mcp_manager.disconnect(name)
        return {"ok": True}

    @app.post("/api/skills/import")
    async def skill_import(body: dict):
        import base64, binascii
        from aon import skill_import as si
        body = body or {}
        try:
            subpath = ""
            if body.get("zip_b64"):
                try:
                    data = base64.b64decode(body["zip_b64"], validate=True)
                except (binascii.Error, ValueError):
                    raise si.ImportError_("zip_b64 is not valid base64")
            elif body.get("url"):
                subpath = si.github_subpath(body["url"])
                data = await si.fetch_github_zip(body["url"])
            elif body.get("skill_md"):
                data = si.zip_from_skill_md(body["skill_md"])
            else:
                raise si.ImportError_("provide zip_b64, url or skill_md")
            found = [s for s in si.scan_zip(data)
                     if not subpath or s["path"] == subpath or s["path"].startswith(subpath + "/")]
            if body.get("dry_run"):
                for s in found:
                    s["exists"] = bool(s["name"]) and (Path(skills_dir) / s["name"] / "SKILL.md").exists()
                return {"skills": found}
            names = body.get("names")
            names = [s["name"] for s in found] if names is None else list(names)
            return si.install_zip(SkillStore(skills_dir), data, names=names,
                                  overwrite=bool(body.get("overwrite")))
        except Exception as e:
            return JSONResponse({"error": str(e) or type(e).__name__})

    @app.get("/api/skills/{name}")
    async def skill_detail(name: str):
        store = SkillStore(skills_dir)
        try:
            info = store.info(name)
        except (ValueError, OSError) as e:
            return JSONResponse({"error": str(e)})
        return {"name": info["name"], "description": info["description"],
                "requires": info["requires"], "files": info["files"],
                "has_evals": "evals/evals.json" in info["files"]}

    @app.get("/api/skills/{name}/report")
    async def skill_report(name: str):
        store = SkillStore(skills_dir)
        try:
            return json.loads(store.read(name, "evals/benchmark.json"))
        except (OSError, ValueError):
            return {}

    @app.post("/api/skills/{name}/evals/run")
    async def skill_run_evals(name: str, body: dict | None = None):
        body = body or {}
        try:
            return await _runner().run_suite(name, int(body.get("output_runs", 1) or 1),
                                             int(body.get("trigger_runs", 3) or 3))
        except (ValueError, OSError, KeyError) as e:
            return JSONResponse({"error": str(e)})

    @app.get("/", response_class=HTMLResponse)
    async def index():
        f = _STATIC / "index.html"
        return f.read_text() if f.exists() else "<h1>AON</h1>"

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        out: asyncio.Queue = asyncio.Queue()
        try:
            first = await websocket.receive_json()
        except Exception:
            return
        goal = first.get("goal", ""); model = first.get("model", cfg.default_model)
        convo = first.get("session_id") or "default"
        task, images, notices = goal, None, []
        if first.get("attachments"):
            try:
                atts = save_attachments(first["attachments"], cfg.workdir)
            except AttachmentError as e:
                await websocket.send_json({"type": "final", "status": "error",
                                           "final": f"Could not take the attachment: {e}"})
                return
            vision = True
            if any(a.is_image for a in atts):
                vision = cfg.chat_vision if model_is_chat(cfg, model) else await vision_check(cfg.ollama_base_url, model)
                if vision:
                    images = image_parts(atts)
                else:
                    notices.append(f"{model} cannot see images; the image was saved and only its "
                                   "file path was given to the agent. Pick a vision model to have it looked at.")
            task = goal + "\n\n" + attachment_note(atts, vision=vision)
            if docs is not None:
                # attached documents also go into the library, so later turns can search them
                from aon.docs import ingest, DocError
                for a in atts:
                    if a.is_image:
                        continue
                    try:
                        d = await ingest(docs, embedder, a.name, a.path.read_bytes(), source="chat")
                        if not d.get("existing"):
                            n = d["chunks"]
                            notices.append(f"Added {d['name']} to the document library "
                                           f"({n} passage{'' if n == 1 else 's'}).")
                    except (DocError, OSError):
                        pass
        try:
            # the newest turns that fit the history budget; older ones are left out (the summary
            # and recall below bring back what matters from them)
            window = db.recent_turn_rows(convo, 20)
            sent = fit_history(window, cfg.history_tokens)
            conversation = [{"role": t["role"], "content": t["content"]} for t in sent]
        except Exception:
            window, sent, conversation = [], [], []
        recalled = ""
        if embedder is not None and (len(window) == 20 or len(sent) < len(window)):
            try:
                await index_turns(db, embedder, convo)
                recalled = await recall_background(db, embedder, convo, goal,
                                                   exclude_ids={t["id"] for t in sent})
            except Exception:
                recalled = ""
        if embedder is not None and cfg.cross_chat_recall:
            try:
                await index_all(db, embedder)
                cross = await other_chats_background(db, embedder, convo, goal)
            except Exception:
                cross = ""
            recalled = "\n\n".join(b for b in (recalled, cross) if b)
        try:
            # the order of what was asked (the summary and recall keep content, not order)
            outline = outline_background(db, convo, {t["id"] for t in sent})
        except Exception:
            outline = ""
        doc_bg = ""
        if docs is not None:
            try:
                from aon.docs import docs_background
                doc_bg = await docs_background(docs, embedder, goal)
            except Exception:
                doc_bg = ""
        recalled = "\n\n".join(b for b in (outline, recalled, doc_bg) if b)
        try:
            db.add_turn(convo, "user", task)
        except Exception:
            pass
        for text in notices:
            await websocket.send_json({"type": "notice", "text": text})

        shown = {"map": False, "last": None}
        async def emit(event: dict):
            if event.get("type") == "map":
                shown["map"] = True
                shown["last"] = event
            await out.put(event)
        async def on_pending(rid, action):
            await out.put({"type": "pending", "req_id": rid, "tool": action.tool, "args": action.args})

        gate = QueueGate(on_pending=on_pending)
        agent = make_agent(gate, emit, model, convo)

        # routes with no start: ask the browser for the user's position (it needs HTTPS and permission)
        pending_loc = {"fut": None}
        async def locate(timeout: float = 20.0):
            fut = asyncio.get_running_loop().create_future()
            pending_loc["fut"] = fut
            await out.put({"type": "need_location"})
            try:
                return await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError:
                return None
            finally:
                pending_loc["fut"] = None
        if hasattr(agent, "locator"):
            agent.locator = locate

        async def run_agent():
            try:
                kw = {"images": images} if images else {}
                try:
                    background = summary_background(db, convo)
                except Exception:
                    background = ""
                background = "\n\n".join(b for b in (background, recalled) if b)
                if background:
                    kw["background"] = background
                try:
                    res = await agent.run(task, conversation=conversation, **kw)
                except Exception as e:
                    # without a final event the page keeps spinning; the error is not kept as a turn
                    await out.put({"type": "final", "status": "error", "final": model_error_text(e, model)})
                    return
                try:
                    # the marker lets the next turn's planner know a map is open ("show it in 3D")
                    note = "\n[map shown]" if shown["map"] else ""
                    last = shown["last"] or {}
                    if getattr(res, "status", "") == "clarify" and last.get("pending") and last.get("targets"):
                        note += "\n" + choices_marker(last)     # lets "yes" / "the second one" pick exactly
                    db.add_turn(convo, "assistant", getattr(res, "final", "") + note)
                except Exception:
                    pass
                try:
                    await maybe_extract(EpisodicMemory(mem_path),
                                        make_chat_llm(cfg, model), goal,
                                        getattr(res, "final", ""))
                except Exception:
                    pass
                if embedder is not None:
                    try:
                        # new facts get vectors; one that repeats or updates an older fact retires a copy
                        await consolidate(EpisodicMemory(mem_path), embedder, make_chat_llm(cfg, model))
                    except Exception:
                        pass
                try:
                    # the reply is already on screen; fold turns that left the recent window into the summary
                    await update_summary(db, make_chat_llm(cfg, model), convo)
                except Exception:
                    pass
                if embedder is not None:
                    try:
                        await backfill(db, embedder)      # this reply's turns, and anything still missing
                    except Exception:
                        pass
            finally:
                await out.put({"type": "__end__"})

        async def sender():
            while True:
                ev = await out.get()
                if ev.get("type") == "__end__":
                    return
                await websocket.send_json(ev)

        async def receiver():
            while True:
                msg = await websocket.receive_json()
                if msg.get("type") == "stop":
                    # cancelling the run closes the in-flight Ollama request, which stops GPU generation
                    if not run_task.done():
                        run_task.cancel()
                        try:
                            db.add_turn(convo, "assistant", "(stopped by the user)")
                        except Exception:
                            pass
                        await out.put({"type": "final", "status": "stopped", "final": "Stopped."})
                    continue
                if msg.get("type") == "location":
                    fut = pending_loc["fut"]
                    if fut is not None and not fut.done():
                        try:
                            lat, lon = float(msg["lat"]), float(msg["lon"])
                            ok = -90 <= lat <= 90 and -180 <= lon <= 180
                        except (KeyError, TypeError, ValueError):
                            ok = False
                        fut.set_result({"lat": lat, "lon": lon, "accuracy": msg.get("accuracy")} if ok else None)
                    continue
                if msg.get("type") == "decision":
                    gate.resolve(int(msg["req_id"]),
                                 Decision(approved=bool(msg["approved"]), edited_args=msg.get("edited_args")))

        run_task = asyncio.create_task(run_agent())
        recv_task = asyncio.create_task(receiver())
        try:
            await sender()
        except WebSocketDisconnect:
            pass
        finally:
            run_task.cancel(); recv_task.cancel()

    return app
