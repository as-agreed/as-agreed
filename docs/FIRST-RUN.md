# First run: see the band and open the Board

This page has four steps. They start from the source and end on the Board.

The band and the Board are not in version 0.1.0. They are in the next release. Until then, use the source.

## 1. Get the code

```
git clone https://github.com/as-agreed/as-agreed
cd as-agreed
```

You must have Python 3.9 or higher. You do not use an account. The code does not use the network.

## 2. Run the example

```
cp -R examples/plugin-hold /tmp/as-agreed-first-run
cd /tmp/as-agreed-first-run
CLAUDE_PROJECT_DIR=$PWD PYTHONPATH=<the clone>/src python3 -m as_agreed hook < events/dropped.json
```

The example is an invented order: "Pay the October invoice, up to 18,400 dollars." The call has no `amount`. The hook stops it.

## 3. See the band

```
PYTHONPATH=<the clone>/src python3 -m as_agreed band .as-agreed/runs/example/ledger.jsonl
```

The output has the labels of the band:

```
Kept as agreed 2 of 3 | held 1 | unchecked 0
```

In Claude Code, the band shows these labels above the prompt. You do not run this command there.

## 4. Open the Board

The hook wrote the Board page in step 2. Open it:

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
