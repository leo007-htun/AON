from pathlib import Path

SYSTEM_PROMPT = """You are a careful coding/dev agent working in a project directory.

Each turn: reflect on what has already been tried (the attempt log) and whether \
your current approach is still viable, keep a short plan, then take ONE next action \
via a tool call. Prefer non-interactive commands (use -y, DEBIAN_FRONTEND=noninteractive). \
Read-only tools run immediately; commands and file writes require human approval, so \
propose exactly what you intend.

When blocked, do not repeat a command that already failed. Escalate instead: adjust and \
retry once, then re-plan a different approach, then research the blocker, then decompose \
the goal, and finally ask the user a specific question. When the goal is achieved, reply \
with a short summary and no further tool call.

Before concluding you lack a program or capability, VERIFY by running commands — `which <bin>`, \
list your workspace — you may already have it. Never claim a gap you did not check. After installing \
a tool, record where it is with the remember tool."""

def load_system_prompt(path) -> str:
    p = Path(path)
    if p.exists():
        t = p.read_text()
        if t.strip():
            return t
    return SYSTEM_PROMPT

def save_system_prompt(path, text: str) -> None:
    Path(path).write_text(text)

def build_messages(goal: str, history: list[dict], memory_text: str = "", skill_list=None, system_prompt=None, tool_summary=None, plan_hint=None, plan_progress=None, images=None, steps=None, background=None) -> list[dict]:
    sys = system_prompt or SYSTEM_PROMPT
    if tool_summary:
        sys += ("\n\nYour capabilities (the ONLY actions you can take):\n" + tool_summary +
                "\n\nMap the request to these tools. If none fits, say plainly you cannot do it and "
                "what tool/capability would be needed — do not improvise unrelated commands. Before "
                "claiming a learned skill is runnable, verify its prerequisites (e.g. `which node`) "
                "and offer to install what is missing.")
    if plan_hint:
        sys += "\n\nYour plan for this turn (from deliberation):\n" + plan_hint
    if plan_progress:
        sys += "\n\n" + plan_progress
    if background and background.strip():
        sys += "\n\n" + background.strip()
    if memory_text.strip():
        sys += ("\n\nMemory about the user and past work (answer questions about the user from "
                "this and from the summary, earlier messages and document passages above; only if "
                "the answer is in none of them, call search_memory / search_conversation / "
                "search_documents before saying you don't know):\n" + memory_text.strip())
    if skill_list:
        lines = "\n".join(f"- {n}: {d}" for n, d in skill_list)
        sys += ("\n\nAvailable skills (call read_skill to load one):\n" + lines +
                "\n\nTo CREATE or improve a skill, first read_skill('creating-skills') for the "
                "method, then author files with write_skill(name, content, path=...). Skills are "
                "folders with a SKILL.md (YAML frontmatter: name, description) plus optional "
                "references/, scripts/, evals/. After authoring, you may run_skill_evals(name). "
                "To build a skill FROM a web page or doc, call draft_skill(url): it fetches the page "
                "and drafts the SKILL.md from its content — do not ask the user what the skill should do.")
    user = [{"type": "text", "text": goal}, *images] if images else goal
    # earlier turns, then the request, then this run's tool calls/results that answer it; putting the
    # request after the results makes models treat it as new and call the same tool again
    return [{"role": "system", "content": sys}, *history, {"role": "user", "content": user}, *(steps or [])]
