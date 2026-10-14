# Using scripts in a skill

Prefer inline instructions. Add a script only when the agent keeps
reinventing the same logic across runs, or a command is too complex to get
right in one shot.

## One-off commands
Reference an existing tool directly and **pin the version**:
`uvx ruff@0.8.0 check .`, `npx eslint@9 --fix .`. State prerequisites in
SKILL.md ("requires Node 18+").

## Bundled scripts
Put them in `scripts/` (author via `write_skill(name, content, path="scripts/x.py")`)
and reference by relative path from the skill root. List them in SKILL.md so
the agent knows they exist. Make scripts self-contained where possible
(Python PEP 723 `# /// script` inline deps run with `uv run`).

## Design scripts for agents
- **No interactive prompts** — agents run non-interactive shells; a TTY
  prompt hangs forever. Take input via flags/env/stdin.
- **`--help`** documents the interface (the agent reads it).
- **Helpful errors:** say what was expected and what to try.
- **Structured output** (JSON/CSV) to stdout; diagnostics to stderr.
- **Idempotent**, with `--dry-run` and `--confirm`/`--force` for destructive ops.
- **Meaningful exit codes**, documented in `--help`.
- **Bounded output** — support `--output`/`--offset` for large results.

## AON note
A skill's script runs through the **gated** `run_command`: bundling a script
never lets it execute without the user's approval. Keep scripts safe-by-default.
