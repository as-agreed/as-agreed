# Changes

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
