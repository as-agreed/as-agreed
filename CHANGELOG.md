# Changes

## 0.1.3, 2026-11-25

- `as-agreed show` prints each term of the record with the person's words for it, from `said`. It marks a quote that is not in the order, and a value that differs from its words. It also marks a number or date with no words, and a hidden character. It marks a binding step that serves no term.
- `as-agreed confirm` is the person's one step. It reads the name from their own terminal. It does not read a name from a pipe. It writes `confirmed_by`, `confirmed_at` and two hashes, of the record and of the binding.
- After the confirm, the hook stops every call while the record or the binding differs from its hash. It does not stop a program that writes both hashes again, or removes them.
- The hook stops a file tool that writes `confirmed_by`, `confirmed_at` or a hash. It also stops the plain forms of an agent's call that runs `as-agreed confirm`.
- A file tool that changes a confirmed record gets the reason that the record is frozen. A record confirmed by hand, as in 0.1.2, still works.
- `as-agreed demo --capture` shows the confirm in one step. The record has a wrong date, and `show` marks it before the confirm.
- In `carried_by`, the skill and the example give a key only to a value that the call uses. A value copied from the order counts as kept and checks nothing.
- The skill says that `in` and `not_in` take a list, and that a date limit is `until` or `from`.
- The first line of the flag names the hold when only an untyped term holds the run. Its count of typed terms does not change.
- The install line is `python3 -m pip install as-agreed`. If the shell does not find `as-agreed`, use `python3 -m as_agreed`.
- `docs/AGENT-SETUP.md` gives the setup and the prompt for your own coding agent, in five steps.

## 0.1.2, 2026-11-23

- The gate holds a binding term that a step changed or lost when no hold stopped it at that step. The term stays held also if a later step gives it back. Before, the flag kept only the last check of each field, so this loss passed.
- A hold may stop a term at its own step. If a later step keeps the term, it still counts as kept because of the check.
- The first line of the flag counts only the fields that code can check. The fields `obligor`, `obligee`, `act` and `modality` that no step declares go on their own line. That line starts with "Not checked by code". The first line keeps its form. The band reads the same counts.
- A term in words only, with no field that code can check, is on the line "Terms not restated, untyped". A `MUST` or `MUST_NOT` term on that line is held, as before. A `SHOULD` or `MAY` term on that line is listed, and it is not held.
- The hook finds the record when the agent starts in a throwaway folder. Examples are a task folder, a CI job or a cloud workdir. First it reads `.as-agreed/` in the start folder, as before. Then it reads `AS_AGREED_RECORD`, the path of a folder with `record.json` and `binding.json`. Then it reads a file `.as-agreed-record` in the start folder or a parent folder. The one line of that file is the path of the folder.
- The hook stops the call if the folder that you name is missing or cannot be read. It also stops the call if that folder is not yours, or if others can write it. It does not read a `.as-agreed-record` file of a different user.
- After you write your name in `confirmed_by`, a file tool cannot change `.as-agreed-record`. It also cannot change `record.json` and `binding.json` in the folder that it names. The band writes its files in `runs/` next to the record that the hook found.
- The plugin starts the check only where it can find a record. If the check cannot start, the call does not run.
- The hook checks each call alone. It does not hold a term that an earlier call lost.
- The package has the example of the hook. `as-agreed example plugin-hold <folder>` copies it into a new or empty folder. Thus `docs/FIRST-RUN.md` starts with `pip install as-agreed`.
- `as-agreed example` does not write into a link or into a folder that others can write. It does not write into a folder with other files.
- `as_agreed.quantity.figures()` keeps a run of digits that is too long for an exact number as text. Before, Python 3.14 stopped it with an error after 4300 digits.

## 0.1.1, 2026-11-23

- In the field check, a declared value in English number words counts as its number. "three" is 3, and "two hundred and seventy-one" is 271. A string of digits stays text, as before.
- `as_agreed.quantity.figures(text)` lists the figures that a text gives, in digits and in words. A gate can use it to fill a field from a draft. `figures("well over three hundred commitments")` gives `[300]`.
- In the field check, a value such as "first", "one half" or "a dozen" is not read as a number.
- `docs/FIRST-RUN.md` gives four steps to the Board: get the code, run the example, see the band, open the Board.
- In Claude Code, the plugin shows a band above the prompt. It counts the binding terms of the task. It shows the terms kept as agreed, the terms held, and the terms that code cannot check. It also counts a term that changed and that no hold stopped, but only when there is one. These are the counts of the flag.
- The band has a button, Board. When you click it, it makes a new page on your machine and opens it. The page lists each binding term of the task: what was agreed, what arrived, and the result. It marks each term that was lost. The page loads nothing and sends nothing.
- The hook writes the records of each check to `.as-agreed/runs/<session>/ledger.jsonl`, with `band.json` and `board.html` next to it. These files hold the values of the calls. A `.gitignore` in `runs/` keeps them out of your commits. Only your user account can read them. They stay on this machine until you delete `.as-agreed/runs/`.
- The hook gives each checked call of a session its own link: `Write#2@1` is the second call. A hold may stop a call, and the call may then run again with the agreed value. The term then counts as kept because of the check.
- `as-agreed band <ledger.jsonl>` prints the band of any run as JSON. With `--board <file>`, it also writes the page.
- The hook does not write through a link in `.as-agreed/runs/`. On Windows, the page opens with `explorer.exe`, not through `cmd`.
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
