# Best practices

## Ground in real expertise
Extract from a real completed task (the steps that worked, the corrections
you made, the I/O formats, the project facts) or synthesize from real
artifacts (runbooks, schemas, review comments, git history). Generic
"follow best practices" content has no value.

## Spend context wisely
When a skill activates its whole body loads into context. Add only what the
agent *wouldn't know*; cut what it already knows. Litmus test per line:
"Would the agent get this wrong without this instruction?" If no, cut it.

## Scope as a coherent unit
Like a function: one clear job. Too narrow → many skills load and conflict;
too broad → hard to trigger. Moderate detail + one working example beats
exhaustive documentation. Keep SKILL.md under ~500 lines / 5,000 tokens;
move depth to references/ with explicit "read when…" pointers.

## Calibrate control to fragility
Give freedom (and explain *why*) where several approaches work. Be
prescriptive ("run exactly this; don't add flags") where operations are
fragile. Provide a default, not a menu of equal options. Teach a procedure
(how to approach the class of problem), not the answer to one instance.

## High-value patterns
- **Gotchas** — concrete corrections to mistakes the agent will make
  (soft-deletes needing `WHERE deleted_at IS NULL`; an id called `user_id`
  here, `uid` there; a `/health` that lies, use `/ready`). Keep in SKILL.md.
  When you correct the agent, add the correction here.
- **Templates** for required output formats (agents pattern-match structure).
- **Checklists** for multi-step workflows with dependencies.
- **Validation loops** — do the work, run a validator, fix, repeat until it passes.
- **Plan-validate-execute** for destructive/batch ops: produce a plan file,
  validate it against a source of truth, then execute.

## Refine with real execution
Run the skill on real tasks; read execution traces, not just outputs. Vague
instructions make the agent thrash; inapplicable ones get followed anyway;
too many options with no default cause wandering. Cut and tighten.
