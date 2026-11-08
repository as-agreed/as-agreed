# Changes

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
