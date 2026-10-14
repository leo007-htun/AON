# Optimizing the description

The `description` carries the entire burden of activation: the agent reads
only name + description up front and loads the body when a task matches.
Hard limit: 1024 characters.

## Write it well
- **Imperative:** "Use this skill when…" (you're telling the agent when to act).
- **User intent, not mechanics:** describe what the user is trying to achieve.
- **Be pushy:** list contexts it applies to, including when the user doesn't
  name the domain ("even if they don't say 'CSV' or 'analysis'").
- **Concise:** a few sentences; it competes with every other skill's description.

Agents skip skills for tasks they can trivially do alone; descriptions matter
most for specialized/unfamiliar work.

## Test triggering
Write ~20 labeled queries (8–10 should-trigger, 8–10 should-not). The useful
positives are should-trigger-but-not-obvious; the useful negatives are
near-misses (share keywords, need something different). Run each ~3× and
compute a trigger-rate (pass threshold 0.5). In AON, `run_skill_evals`
reads the `triggers` list in evals.json and reports the trigger-rate.

## Iterate without overfitting
Split queries train/validation (60/40). Use only train failures to guide
edits; don't paste literal keywords from failed queries — generalize to the
underlying category. ~5 iterations; keep the version with the best validation
pass rate (not necessarily the last). Stay under 1024 chars.

## Before / after
```
# Before
description: Process CSV files.
# After
description: >
  Analyze CSV/tabular data — summary stats, derived columns, charts, cleaning.
  Use when the user has a CSV, TSV, or Excel file and wants to explore,
  transform, or visualize it, even if they don't say "CSV" or "analysis".
```
