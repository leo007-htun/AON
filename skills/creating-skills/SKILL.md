---
name: creating-skills
description: How to author and evaluate a reusable AON skill in the Agent Skills (SKILL.md) format. Use when asked to create, write, improve, or evaluate a skill, or to add a capability as a reusable procedure.
---

# Creating a skill

A skill is a folder `skills/<name>/` with a `SKILL.md` and optional
`references/`, `scripts/`, `assets/`, `evals/`. Author files with
`write_skill(name, content, path=...)`, read them with
`read_skill(name, path=...)`, list them with `skill_files(name)`, and
evaluate with `run_skill_evals(name)`.

## SKILL.md format

```
---
name: <kebab-case-name>     # matches the folder
description: <one line>     # what it does AND when to use it (<=1024 chars)
---

<markdown instructions the agent follows when the skill activates>
```

The agent sees only `name` + `description` up front (progressive disclosure)
and loads the body only when the description matches the task.

## Workflow

1. Ground it in real expertise, not generic advice (see `references/best-practices.md`).
2. Write a description that names the triggers (see `references/descriptions.md`).
3. Write a tight, stepwise body. Add a **Gotchas** section for facts the model
   would get wrong without being told.
4. For heavy content, split into `references/<x>.md` and tell the agent *when*
   to read each ("read references/api-errors.md if the API returns non-200").
5. Bundle a tested script in `scripts/` only when the agent keeps reinventing
   the same logic (see `references/scripts.md`). Scripts run via the gated
   `run_command`.
6. Evaluate: write `evals/evals.json` and run `run_skill_evals(name)` to
   measure with-skill vs without-skill and description trigger-rate
   (see `references/evals.md`).

## When to read the references

- `references/best-practices.md` — scoping, what to include/omit, Gotchas,
  templates, checklists, validation loops, calibrating control.
- `references/descriptions.md` — writing and testing the description.
- `references/evals.md` — building and running the eval suite.
- `references/scripts.md` — one-off commands vs bundled scripts, agent-friendly design.

## A skill is not a tool

A skill is only instructions. It cannot grant a capability the agent lacks
(e.g. sending email needs an email *tool* with credentials). If the task
needs a new capability, say so — don't write a skill that pretends to act.

## Declaring prerequisites

If a skill needs external tools/binaries to run, declare them in frontmatter:
`requires: node npx`. AON verifies these (e.g. `which node`) before claiming the
skill is runnable, and offers to install what is missing.
