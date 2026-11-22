# Changes

## Unreleased

- In Claude Code, the plugin shows a band above the prompt. It counts the binding terms of the task. It shows the terms kept as agreed, the terms held, and the terms that code cannot check. It also counts a term that changed and that no hold stopped, but only when there is one. These are the counts of the flag.
- The band has a button, Board. It opens a page on your machine. The page lists each binding term of the task: what was agreed, what arrived, and the result. It marks each term that was lost. The page loads nothing and sends nothing.
- The hook writes the records of each check to `.as-agreed/runs/<session>/ledger.jsonl`, with `band.json` and `board.html` next to it. These files hold the values of the calls. A `.gitignore` in `runs/` keeps them out of your commits.
- The hook gives each checked call of a session its own link: `Write#2@1` is the second call. A hold may stop a call, and the call may then run again with the agreed value. The term then counts as kept because of the check.
- `as-agreed band <ledger.jsonl>` prints the band of any run as JSON. With `--board <file>`, it also writes the page.
- The band needs Claude Code 2.1.290 or later, in the terminal or the desktop app.

## 0.1.0, 2026-11-15

- A step can have a `binding`: the commitments that it `serves`, its `kind`, its `author` and its `recipient`. As-Agreed then checks only those commitments at that step.
- A key that the step must have and that is not in its output gives `DROPPED`. As-Agreed does the same with `on_missing_key="undeclared"`. A field that the step does not have to give is not `DROPPED`. A different value of that field counts.
- A `takes_effect` step must have `obligor` only if its `author` or its `recipient` is the `obligor`.
- After each run, the report and the demo show the flag. The first line counts the binding terms that arrived as agreed. It also counts the terms that a hold stops, and the terms that code cannot check. It names a changed term that no hold stops. The next line counts the terms that arrived as agreed only because a hold made the step run again. Each step writes the flag to `ledger.jsonl`. Thus each step adds one record and one more read of `ledger.jsonl`.
- Give `baseline_minutes` to `report`, or `--baseline-minutes` to `as-agreed`, as your time for a manual check of one run. Then the flag shows that time when no person must look at the run.
- `as-agreed hook` reads a Claude Code `PreToolUse` input. It checks a tool call that `.as-agreed/binding.json` names against `.as-agreed/record.json`. It stops the call if a term changed, or if a must term is missing. It also stops the call if `confirmed_by` has no name, or if the check cannot run. A file tool cannot write `confirmed_by`. After a person writes it, a file tool cannot change `record.json` or `binding.json`.
- The repository is also a Claude Code plugin, `as-agreed`, with a skill and the hook. The example `examples/plugin-hold/` has a record, a binding, and a tool call for each result: pass, `DROPPED` and `WIDENED`.
- `docs/WORDLIST.md` adds the names hook, plugin, skill, binding, flag, Claude and Code.

## 0.0.3, 2026-11-07

- `as-agreed import langsmith` reads LangSmith runs: a JSON list, the API data of `/runs/query`, one run with its `child_runs`, a `.jsonl` file, or a CSV file with the same column names. It orders the runs by `dotted_order`. The order is the input of the run whose `id` is the `trace_id`.
- Each link from LangSmith keeps the `status` of its run, and its `error`. A run with `status` `error` and no output is a link with the text `error: ...`.
- `as-agreed import otlp` reads OpenTelemetry spans: OTLP JSON, one `TracesData` in a file or one on each line. The type of a step is `gen_ai.operation.name`. An agent is `gen_ai.agent.name` or `gen_ai.agent.id`. The text comes from `gen_ai.output.messages` or `gen_ai.tool.call.result`.
- A span with no text gives a link with no text. The output line tells how many, and names `OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT`. A trace with no text for the order stops with code 2 and tells you to use `--commitment`.
- If two traces in one import have the same first 8 characters in their id, `import langsmith` and `import otlp` name each stage folder with the full id.
- The tests `I4` and `I5` run on this new code. Thus 78 of the 82 tests of `conformance` pass, and 4 do not run.
- `README.md` names the three commands. Its line on version 0.2 is removed.

## 0.0.2, 2026-11-07

- The README starts with `pip install as-agreed`, since 0.0.1 is on PyPI.
- `as-agreed import langfuse` reads Langfuse traces: the API data, or a file from `observations_v2/`, with `camelCase` or `snake_case` keys. It writes one stage folder for each trace. With `--commitment`, the order is your file. Without it, the order is the input at the top of the trace, marked `proxy: true`.
- In each stage folder, `trace-meta.json` names each agent that the trace declares, each hand-off, and `delivered`.
- The test `I3` runs on this new code. Thus 76 of the 82 tests of `conformance` pass, and 6 do not run.

- In the last stage, the text gate marks `not found` with no date as `DANGER` (`undated absence`).
- In the last stage, the text gate marks `the only` with no dated source as `DANGER` (`unsourced superlative`).
- A line with the mark `[!]` is a claim, also when it is short.
- The first lines of `README.md` are new: they tell what the library does today.
- `readings/README.md` uses the name "hand-off" for the point between one step and the next.
- `README.md` tells which result a value below an agreed minimum gets: `WIDENED`.
- `README.md` tells which result a key such as `done` gets when it has `false` where `true` was agreed: `WEAKENED`.

## 0.0.1, 2026-11-07

- The first public release.
