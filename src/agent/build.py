from pathlib import Path
from agent.config import Config
from agent.llm_client import LLMClient
from agent.tools.base import ToolRegistry
from agent.tools.files_read import ReadFileTool, ListDirTool
from agent.tools.files_write import WriteFileTool, EditFileTool
from agent.tools.shell import RunCommandTool
from agent.tools.web import WebFetchTool, WebSearchTool
from agent.approval_gate import ApprovalGate, AutoGate
from agent.engine import Agent
from agent.memory import MemoryStore, RememberTool
from agent.skills import SkillStore, ReadSkillTool, WriteSkillTool, SkillFilesTool
from agent.prompts import load_system_prompt

def mem_store(cfg: Config) -> MemoryStore:
    return MemoryStore(Path("memory") / "episodes.db")

def skill_store(cfg: Config) -> SkillStore:
    return SkillStore(Path("skills"))

def build_registry(cfg: Config, *, workdir=None, skills_root=None, mem_path=None, include_evals=True, mcp_manager=None, model=None, embedder=None) -> ToolRegistry:
    wd = workdir or cfg.workdir
    sk = Path(skills_root) if skills_root else Path("skills")
    mp = Path(mem_path) if mem_path else (Path("memory") / "episodes.db")
    r = ToolRegistry()
    r.register(ReadFileTool(wd))
    r.register(ListDirTool(wd))
    r.register(WriteFileTool(wd))
    r.register(EditFileTool(wd))
    r.register(RunCommandTool(wd, cfg.command_timeout))
    r.register(WebFetchTool())
    r.register(WebSearchTool())
    from agent.tools.map_tool import ShowMapTool
    from agent.geo import default_geo
    r.register(ShowMapTool(default_geo()))
    ms = MemoryStore(mp); ss = SkillStore(sk)
    r.register(RememberTool(ms))
    from agent.memory_extract import SearchMemoryTool, LearnUrlTool
    r.register(SearchMemoryTool(ms, embedder=embedder))
    async def _fetch(url):
        res = await WebFetchTool().run({"url": url})
        return res.output if res.ok else ""
    # tools that call the model themselves use the one picked for this chat
    from agent.backends import make_chat_llm
    tool_llm = make_chat_llm(cfg, model)
    r.register(LearnUrlTool(_fetch, tool_llm, ms))
    from agent.skill_author import DraftSkillTool
    r.register(DraftSkillTool(_fetch, tool_llm, ss))
    r.register(ReadSkillTool(ss))
    r.register(WriteSkillTool(ss))
    r.register(SkillFilesTool(ss))
    from agent.capabilities import CapabilitiesTool
    r.register(CapabilitiesTool(r, ss))
    if include_evals:
        from agent.skill_evals import RunSkillEvalsTool
        r.register(RunSkillEvalsTool(cfg))
    if mcp_manager is not None:
        # interactive app only (not the eval sandbox): gated tools that extend AON
        from agent.install_tools import AddMcpServerTool, ImportSkillsTool
        r.register(AddMcpServerTool(mcp_manager))
        r.register(ImportSkillsTool(ss))
        from agent.model_tools import PullModelTool, ListModelsTool, RemoveModelTool
        r.register(PullModelTool(cfg.ollama_base_url))
        r.register(ListModelsTool(cfg.ollama_base_url))
        r.register(RemoveModelTool(cfg.ollama_base_url))
        from agent.mcp_tools import McpTool
        for spec in mcp_manager.tools():
            r.register(McpTool(mcp_manager, spec))
    return r

def make_router(cfg, llm, reg, memory=None):
    from agent.router import deliberate
    tool_names = {t.name for t in reg.list()}
    tsum = "\n".join(f"- {t.name}: {((t.description or '').splitlines() or [''])[0][:100]}"
                     for t in reg.list())
    async def _router(goal, conversation=None):
        from agent.router import ensure_map, pending_place_question, Plan
        from agent.geo import map_hint
        map_open = any("[map shown]" in (t.get("content") or "") for t in (conversation or [])[-6:])
        pending = pending_place_question(conversation)
        hinted = pending or map_hint(goal, map_open)
        if hinted:
            # map-like message: the narrow extractor (~2 s) first; if it finds a map request the big
            # planner (~9 s, and it invented "Melbourne" places in testing) is not needed at all
            quick = await ensure_map(Plan("", "map", [], False, None, None), goal, llm, conversation, map_open,
                                     pending_question=pending)
            if quick.map:
                return quick
        ssum = "\n".join(f"- {n}: {d}" for n, d in skill_store(cfg).list())
        plan = await deliberate(goal, llm, tools_summary=tsum, skills_summary=ssum,
                                memory_text=(memory or mem_store(cfg)).context_for(goal), valid_tools=tool_names,
                                conversation=conversation)
        # the extractor already ran (and found nothing) for hinted messages: don't ask it twice
        return await ensure_map(plan, goal, llm, conversation, map_open, allow_extract=not hinted)
    return _router

def build_agent(cfg: Config, gate: ApprovalGate) -> Agent:
    reg = build_registry(cfg)
    llm = LLMClient(cfg.ollama_base_url, cfg.default_model)
    return Agent(llm, reg, gate, max_steps=cfg.max_steps,
                 memory=mem_store(cfg), skills=skill_store(cfg),
                 system_prompt=load_system_prompt(Path("system_prompt.txt")),
                 router=make_router(cfg, llm, reg))

def build_eval_agent(cfg, workspace, skills_dir, model, llm) -> Agent:
    reg = build_registry(cfg, workdir=workspace, skills_root=skills_dir,
                         mem_path=Path(workspace) / "_eval_mem.md", include_evals=False)
    return Agent(llm, reg, AutoGate(approve=True), max_steps=cfg.max_steps,
                 memory=MemoryStore(Path(workspace) / "_eval_mem.md"),
                 skills=SkillStore(skills_dir),
                 system_prompt=load_system_prompt(Path("system_prompt.txt")))
