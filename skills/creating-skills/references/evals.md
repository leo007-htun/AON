# Evaluating a skill

Store test cases in `evals/evals.json`, then run `run_skill_evals(name)`.
AON runs each case twice — with the skill and without it — in isolated,
disposable sandboxes, grades assertions, tests description trigger-rate, and
writes `evals/benchmark.json` with the pass-rate/time/token delta.

## evals.json shape
```json
{
  "evals": [
    { "id": "soft-delete",
      "prompt": "Write SQL to count active users.",
      "expected_output": "A query counting active users only.",
      "files": [],
      "assertions": ["The query includes WHERE deleted_at IS NULL"] }
  ],
  "triggers": [
    { "query": "how many users do we have?", "should_trigger": true },
    { "query": "write a fibonacci function", "should_trigger": false }
  ]
}
```

## Writing good test cases
- Start with 2–3; vary phrasing, detail, formality; cover one edge case;
  use realistic context (file paths, column names).
- `files` are copied from `evals/` into the sandbox before the run.

## Writing good assertions
Verifiable, specific, countable ("includes a bar chart image", "exactly 3
months", "valid JSON"). Not "output is good" (vague) or exact-phrase matches
(brittle). Grading requires concrete evidence for a PASS.

## Reading the benchmark
`summary.delta.pass_rate` is the value the skill adds (with minus without).
A positive delta means the skill helps. `trigger_pass_rate` is the fraction
of trigger queries that behaved as labeled. If with and without both pass,
the model didn't need the skill for that case — tighten the case or the skill.
Local models are nondeterministic; raise `output_runs`/`trigger_runs` for
more stable numbers.
