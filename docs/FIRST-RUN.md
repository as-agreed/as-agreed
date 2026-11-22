# First run: see the band and open the Board

This page has four steps. They start from the package and end on the Board.

## 1. Install the package

```
pip install as-agreed
```

You must have Python 3.9 or higher. You do not use an account. The code does not use the network.

## 2. Run the example

```
as-agreed example plugin-hold run
cd run
CLAUDE_PROJECT_DIR=$PWD as-agreed hook < events/dropped.json
```

The first command copies the example into a new folder, `run`. The example is an invented order: "Pay the October invoice, up to 18,400 dollars." The call has no `amount`. The hook stops it.

## 3. See the band

```
as-agreed band .as-agreed/runs/example/ledger.jsonl
```

The output is JSON. It starts with the labels of the band:

```
{"labels": ["Kept as agreed 2 of 3", "held 1", "unchecked 0"], "terms": 3, ...
```

In Claude Code, the band shows these three labels above the prompt. You do not run this command there.

## 4. Open the Board

The hook wrote the Board page in step 2. Open it. On macOS, use `open`. On Linux, use `xdg-open`:

```
open .as-agreed/runs/example/board.html
```

The page lists each term: what was agreed, what arrived, and the result. It marks the term that was lost: `limit.amount`. The page loads nothing and sends nothing.

## In Claude Code

```
/plugin marketplace add as-agreed/as-agreed
/plugin install as-agreed@as-agreed
```

Claude Code 2.1.290 or later shows the band in the terminal and in the desktop app. The Board button opens a new page on your machine.

## Limits

The band counts only the calls that `.as-agreed/binding.json` names. The page keeps the values of each checked call in `.as-agreed/runs/` until you delete that folder.

## From the source

The example is also in the source, in `examples/plugin-hold`. Clone the repository and set `PYTHONPATH` to its `src` folder. Then use `python3 -m as_agreed` in place of `as-agreed`.
